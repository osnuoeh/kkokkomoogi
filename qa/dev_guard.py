"""Local development opt-in, not an authentication mechanism."""
import os
import streamlit as st


def require_local_development():
    enabled = os.getenv('KKOKKOMOOGI_DEV_LOCAL') == '1'
    loopback = st.get_option('server.address') in ('127.0.0.1', 'localhost', '::1')
    if not (enabled and loopback):
        st.error('개발자 도구는 로컬 명시 실행만 허용합니다. KKOKKOMOOGI_DEV_LOCAL=1과 --server.address 127.0.0.1을 설정해 주세요.')
        st.stop()
