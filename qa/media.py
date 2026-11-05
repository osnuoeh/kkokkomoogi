"""Bounded per-session PDF rasterization, no external file upload or disk export."""
from dataclasses import dataclass, field
from io import BytesIO
from .content import profile


@dataclass(frozen=True)
class PageImage:
    page_id: str
    file: str
    number: int
    data: bytes = field(repr=False)


def choose(pages, count=3):
    ranked=sorted(pages,key=lambda p:profile(p)['score'],reverse=True)
    return [p.id for p in ranked[:count]]


def render(sources, pages, selected_ids):
    import pypdfium2 as pdfium
    if len(selected_ids)>4:
        raise ValueError('시각 분석은 요청당 최대 4페이지입니다.')
    images=[]
    for page in pages:
        if page.id not in selected_ids:
            continue
        # Sources are keyed by stable page IDs, never an ambiguous filename.
        data=sources.get(page.id)
        if data is None:
            continue  # PPTX is text-only until user exports to PDF.
        doc=pdfium.PdfDocument(data)
        try:
            obj=doc[page.number-1]
            try:
                width,height=obj.get_size()
                bitmap=obj.render(scale=min(1.5,1200/max(width,height)))
                try:
                    pil=bitmap.to_pil(); output=BytesIO()
                    try:
                        rgb=pil.convert('RGB')
                        try:
                            rgb.save(output,format='JPEG',quality=75)
                        finally:
                            rgb.close()
                    finally:
                        pil.close()
                    image=PageImage(page.id,page.file,page.number,output.getvalue())
                finally:
                    bitmap.close()
            finally:
                obj.close()
        finally:
            doc.close()
        images.append(image)
    if sum(len(i.data) for i in images)>4*1024*1024:
        raise ValueError('선택 이미지가 큽니다. 페이지 수를 줄여 주세요.')
    return tuple(images)
