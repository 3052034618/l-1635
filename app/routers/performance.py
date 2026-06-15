from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import PerformanceReport as PerformanceReportSchema
from app.services.performance import PerformanceReportService, get_performance_report_service

router = APIRouter(prefix="/performance-reports", tags=["绩效报表"])


@router.post("/generate", response_model=List[PerformanceReportSchema], summary="生成月度绩效报表")
def generate_report(
    year: int,
    month: int,
    route_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    service = get_performance_report_service(db)
    return service.generate_monthly_report(year, month, route_id)


@router.get("", response_model=List[PerformanceReportSchema], summary="查询绩效报表")
def list_reports(
    report_month: Optional[str] = None,
    route_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    service = get_performance_report_service(db)
    return service.get_reports(report_month, route_id)


@router.get("/summary", summary="获取月度汇总数据")
def get_summary(report_month: str, db: Session = Depends(get_db)):
    service = get_performance_report_service(db)
    return service.get_summary(report_month)


@router.get("/export", summary="导出绩效报表Excel")
def export_report(
    report_month: Optional[str] = None,
    route_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    service = get_performance_report_service(db)
    output = service.export_to_excel(report_month, route_id)

    filename = f"performance_report_{report_month or 'all'}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
