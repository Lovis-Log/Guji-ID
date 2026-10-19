import logging
import mimetypes
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from app.db import PROJECT_ROOT, TABLES, connect_db, default_db_path, init_db
from app.catalog import curated_examples, get_alias, get_person, public_counts, search_people
from app.normalization import normalize_name
from app.auto_catalog import pipeline_counts
from app.field_quality import has_quality
from app.source_catalog import get_source,get_raw_record
from app.archive_display import figure_gallery, figures_for_person, scans_for_person
from app import classics

logger = logging.getLogger(__name__)
mimetypes.add_type("image/webp", ".webp")
ASSET_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(ASSET_ROOT / "templates"))
templates.env.filters["url_id"] = lambda value: quote(value, safe="")


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = Path(db_path) if db_path is not None else default_db_path()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # 不在 import app 时建库，测试可显式传入独立临时数据库。
        init_db(application.state.db_path)
        yield

    application = FastAPI(title="GuJi ID · 古籍人物规范档", version="0.6.0", lifespan=lifespan)
    application.state.db_path = path
    application.mount("/static", StaticFiles(directory=str(ASSET_ROOT / "static")), name="static")

    def page_error(request, status, message):
        if "text/html" in request.headers.get("accept", ""):
            return templates.TemplateResponse(request=request, name="error.html", status_code=status,
                context={"message": message, "status_code": status})
        return None

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            html = page_error(request, 404, "未找到请求的页面，请返回检索已收录的人物或称呼。")
            if html is not None:
                return html
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": f"http_{exc.status_code}", "message": str(exc.detail)}},
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        html = page_error(request, 422, "输入内容过长或格式不正确，请输入不超过100字的姓名或称呼。")
        if html is not None:
            return html
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": "请求参数格式不正确"}})

    @application.exception_handler(sqlite3.Error)
    async def database_error(request: Request, exc: sqlite3.Error):
        logger.error("数据库操作失败", exc_info=(type(exc), exc, exc.__traceback__))
        html = page_error(request, 503, "数据库暂时不可用，请稍后重试。")
        return html if html is not None else JSONResponse(status_code=503, content={"error": {"code": "database_unavailable", "message": "数据库暂时不可用，请稍后重试"}})

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error("未处理的服务错误", exc_info=(type(exc), exc, exc.__traceback__))
        html = page_error(request, 500, "服务暂时发生错误，请稍后重试。")
        return html if html is not None else JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "服务暂时发生错误，请稍后重试"}})

    @application.get("/search", response_class=HTMLResponse, name="search")
    @application.get("/", response_class=HTMLResponse)
    def home(request: Request, q: str | None = Query(default=None, max_length=100)):
        with connect_db(application.state.db_path) as connection:
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
            verified_aliases = connection.execute("SELECT COUNT(*) FROM aliases WHERE verification_status = 'verified'").fetchone()[0]
            held_count=0
            if q and has_quality(connection):
                held_count=connection.execute("SELECT count(*) FROM quality_quarantine WHERE instr(source_json,?)>0",(q,)).fetchone()[0]
        query = normalize_name(q or "")
        results = search_people(application.state.db_path, query)
        selected_id = results[0]["person_id"] if len(results) == 1 else None
        archive_scans = scans_for_person(application.state.db_path, selected_id) if selected_id else []
        return templates.TemplateResponse(request=request, name="index.html", context={
            "counts": counts, "verified_aliases": verified_aliases,
            "public_counts": public_counts(application.state.db_path),
            "q": query, "searched": q is not None,
            "results": results,
            "archive_scans": archive_scans,
            "archive_scan": archive_scans[0] if archive_scans else None,
            "archive_figures": figures_for_person(application.state.db_path, selected_id) if selected_id else [],
            "archive_figure_gallery": figure_gallery(application.state.db_path),
            "examples": curated_examples(application.state.db_path),
            "pipeline": pipeline_counts(application.state.db_path),
            "held_count":held_count,
        })

    def unavailable(request, kind):
        return templates.TemplateResponse(request=request, name="unavailable.html", status_code=404, context={"kind": kind})

    @application.get("/classics", response_class=HTMLResponse, name="classic_index")
    def classic_index(request: Request):
        return templates.TemplateResponse(request=request, name="classic_index.html",
            context={"works": classics.work_index(application.state.db_path)})

    @application.get("/classics/{work_id}", response_class=HTMLResponse, name="classic_work")
    def classic_work(request: Request, work_id: str):
        work = classics.get_work(application.state.db_path, work_id)
        if work is None:
            return unavailable(request, "古籍目录")
        return templates.TemplateResponse(request=request, name="classic_work.html",
            context={"work": work})

    @application.get("/classics/{work_id}/{chapter_id}", response_class=HTMLResponse, name="classic_chapter")
    def classic_chapter(request: Request, work_id: str, chapter_id: str,
                        evidence: str = Query(default="", max_length=200),
                        person: str = Query(default="", max_length=200)):
        data = classics.reader_data(application.state.db_path, work_id, chapter_id,
                                    person_id=person, selected=evidence)
        if data is None:
            return unavailable(request, "古籍篇章")
        return templates.TemplateResponse(request=request, name="classic_chapter.html", context=data)

    @application.get("/people/{person_id:path}", response_class=HTMLResponse)
    def person_detail(request: Request, person_id: str, q: str = Query(default="", max_length=100)):
        person = get_person(application.state.db_path, person_id)
        if person is None:
            return unavailable(request, "人物档案")
        archive_scans = scans_for_person(application.state.db_path, person_id)
        return templates.TemplateResponse(request=request, name="person.html", context={
            "person": person, "q": normalize_name(q),
            "archive_scans": archive_scans,
            "archive_scan": archive_scans[0] if archive_scans else None,
            "archive_figures": figures_for_person(application.state.db_path, person_id),
            "archive_figure_gallery": figure_gallery(application.state.db_path),
            "classical": classics.person_library(application.state.db_path, person_id),
        })

    @application.get("/aliases/{alias_id:path}", response_class=HTMLResponse)
    def alias_detail(request: Request, alias_id: str):
        alias = get_alias(application.state.db_path, alias_id)
        if alias is None:
            return unavailable(request, "称呼证据")
        return templates.TemplateResponse(request=request, name="alias.html", context={"alias": alias})

    @application.get('/sources/{source_id:path}',response_class=HTMLResponse)
    def source_detail(request:Request,source_id:str,person_id:str=Query(default='',max_length=100),page:int=Query(default=1,ge=1,le=10000)):
        source=get_source(application.state.db_path,source_id,person_id=person_id,page=page)
        if source is None:return unavailable(request,'文献来源')
        return templates.TemplateResponse(request=request,name='source.html',context={'source':source})

    @application.get('/records/{record_type}/{record_id:path}',name='raw_record')
    def raw_record(record_type:str,record_id:str):
        record=get_raw_record(application.state.db_path,record_type,record_id)
        if record is None:raise HTTPException(status_code=404,detail='原始记录不存在或当前不可公开')
        return JSONResponse(content=record,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @application.get("/health")
    def health():
        with connect_db(application.state.db_path) as connection:
            # 读取全部核心表，避免空文件或缺表时错误地返回健康。
            for table in TABLES:
                connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise sqlite3.OperationalError("foreign keys disabled")
        return {"status": "ok", "database": "ok", "stage": 7}

    @application.get("/templates/{table}.csv", response_class=FileResponse)
    def download_template(table: str):
        if table not in TABLES:
            raise HTTPException(status_code=404, detail="CSV 模板不存在")
        return FileResponse(
            PROJECT_ROOT / "data" / "templates" / f"{table}.csv",
            media_type="text/csv; charset=utf-8", filename=f"{table}.csv",
        )

    return application


app = create_app()

