"""Persistent shared reservation via Redis REST + one atomic Lua operation.

No model payload, API key, filename, answer or raw session identifier is stored.
The lifetime ledger has no expiry and is never automatically reinitialized.
"""
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
import httpx


class QuotaUnavailable(Exception):
    pass


class QuotaExceeded(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Usage limit reached')


@dataclass(frozen=True)
class Limits:
    session_calls: int = 10
    daily_calls: int = 100
    total_calls: int = 500
    daily_units: int = 8_000_000
    total_units: int = 40_000_000
    minute_calls: int = 10
    min_interval: int = 2


INIT_LUA = '''
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'schema', '1', 'calls_total', '0', 'units_total', '0',
    'day', '-1', 'calls_day', '0', 'units_day', '0', 'minute', '-1', 'calls_minute', '0')
return 1
'''

RESERVE_LUA = '''
-- Read all state before writing; corrupted or expiring ledgers fail closed.
local root = KEYS[1]
if redis.call('TTL', root) ~= -1 then return {-1, 'ledger'} end
local data = redis.call('HMGET', root, 'schema', 'calls_total', 'units_total',
    'day', 'calls_day', 'units_day', 'minute', 'calls_minute')
if data[1] ~= '1' then return {-1, 'ledger'} end
for i=2,8 do
    local number = tonumber(data[i])
    if not number or number ~= math.floor(number) then return {-1, 'ledger'} end
    if i ~= 4 and i ~= 7 and number < 0 then return {-1, 'ledger'} end
    if (i == 4 or i == 7) and number < -1 then return {-1, 'ledger'} end
end
local sc = tonumber(ARGV[1])
local dc = tonumber(ARGV[2])
local tc = tonumber(ARGV[3])
local du = tonumber(ARGV[4])
local tu = tonumber(ARGV[5])
local mc = tonumber(ARGV[6])
local interval = tonumber(ARGV[7])
local units = tonumber(ARGV[8])
local now = tonumber(redis.call('TIME')[1])
local day = math.floor((now + 32400) / 86400) -- Asia/Seoul day boundary
local minute = math.floor(now / 60)
local total = tonumber(data[2])
local all_units = tonumber(data[3])
local day_calls = tonumber(data[5])
local day_units = tonumber(data[6])
local minute_calls = tonumber(data[8])
if tonumber(data[4]) ~= day then day_calls=0; day_units=0 end
if tonumber(data[7]) ~= minute then minute_calls=0 end
local session = redis.call('HMGET', KEYS[2], 'calls', 'last')
local session_calls = 0
local last = 0
if session[1] then
    session_calls=tonumber(session[1]); last=tonumber(session[2])
    if not session_calls or not last or session_calls < 0 or last < 0 or
        session_calls ~= math.floor(session_calls) or last ~= math.floor(last) then
        return {-1, 'session'}
    end
end
if session_calls >= sc then return {0, 'session'} end
if total >= tc or all_units + units > tu then return {0, 'total'} end
if day_calls >= dc or day_units + units > du then return {0, 'daily'} end
if minute_calls >= mc then return {0, 'minute'} end
if session_calls > 0 and now - last < interval then return {0, 'interval'} end
redis.call('HSET', root, 'calls_total', total+1, 'units_total', all_units+units,
    'day', day, 'calls_day', day_calls+1, 'units_day', day_units+units,
    'minute', minute, 'calls_minute', minute_calls+1)
redis.call('HSET', KEYS[2], 'calls', session_calls+1, 'last', now)
redis.call('EXPIRE', KEYS[2], 604800)
return {1, 'reserved'}
'''


class RedisQuotaStore:
    def __init__(self, url, token, namespace):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or not token or not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}', namespace)):
            raise QuotaUnavailable('Invalid quota configuration')
        self._url, self._token = url.rstrip('/'), token
        self.root = '{' + namespace + '}:ledger'
        self.prefix = '{' + namespace + '}:session:'

    def __repr__(self):
        return 'RedisQuotaStore(<server configuration>)'

    def _command(self, command):
        try:
            response = httpx.post(self._url, headers={'Authorization': 'Bearer '+self._token},
                                  json=command, timeout=5, follow_redirects=False)
            response.raise_for_status()
            body = response.json()
            if 'error' in body or 'result' not in body:
                raise ValueError()
            return body['result']
        except Exception:
            raise QuotaUnavailable('Quota service unavailable') from None

    def initialize(self):
        # Operator action only. Existing ledgers are NEVER reset or overwritten.
        result = self._command(['EVAL', INIT_LUA, 1, self.root])
        if result not in (0, 1):
            raise QuotaUnavailable('Invalid quota response')
        return result == 1

    def reserve(self, session_id, units, limits):
        if not session_id or not isinstance(units, int) or units <= 0:
            raise QuotaUnavailable('Invalid reservation')
        session_key = self.prefix + hashlib.sha256(session_id.encode()).hexdigest()
        result = self._command(['EVAL', RESERVE_LUA, 2, self.root, session_key,
                                limits.session_calls, limits.daily_calls, limits.total_calls,
                                limits.daily_units, limits.total_units, limits.minute_calls,
                                limits.min_interval, units])
        if not isinstance(result, list) or len(result) != 2:
            raise QuotaUnavailable('Invalid quota response')
        if result[0] == 1 and result[1] == 'reserved':
            return
        if result[0] == 0 and result[1] in ('session', 'daily', 'total', 'minute', 'interval'):
            raise QuotaExceeded(result[1])
        raise QuotaUnavailable('Quota ledger missing or invalid')
