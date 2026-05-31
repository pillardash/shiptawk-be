from pydantic import Field

from app.shared.schemas import ApiSchema


class PaginationParams(ApiSchema):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class PageMeta(ApiSchema):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class PageResponse[T](ApiSchema):
    items: list[T]
    meta: PageMeta


def build_page_meta(page: int, page_size: int, total_items: int) -> PageMeta:
    total_pages = max((total_items + page_size - 1) // page_size, 1)
    return PageMeta(
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_previous=page > 1,
    )
