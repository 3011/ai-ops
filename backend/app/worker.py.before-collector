import asyncio, os, socket
from datetime import timedelta
from sqlalchemy import func, select, update
from app.config import get_settings
from app.db import SessionLocal, init_database
from app.models import AnalysisRun, OutboxJob

settings = get_settings()
worker_id = f"{socket.gethostname()}:{os.getpid()}"

def utcnow():
    from datetime import UTC, datetime
    return datetime.now(UTC)

async def claim() -> int | None:
    async with SessionLocal() as session:
        async with session.begin():
            job = await session.scalar(select(OutboxJob).where(OutboxJob.status.in_(["pending", "retry"]), OutboxJob.available_at <= utcnow()).order_by(OutboxJob.priority, OutboxJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not job: return None
            job.status = "processing"; job.locked_at = utcnow(); job.locked_by = worker_id; job.attempts += 1
            await session.flush(); return job.id

async def process(job_id: int) -> None:
    async with SessionLocal() as session:
        job = await session.get(OutboxJob, job_id)
        incident_id = int(job.payload["incident_id"])
        session.add(AnalysisRun(incident_id=incident_id, status="skipped", model=None, result={"summary": "告警已可靠接入；Prometheus/Loki 证据采集与 LLM 分析将在下一迭代启用。", "root_cause_hypotheses": [], "recommended_checks": ["在 Grafana 中查看对应时间窗口的指标和日志。"]}, finished_at=utcnow()))
        job.status = "succeeded"; job.finished_at = utcnow(); job.locked_at = None; job.locked_by = None
        await session.commit()

async def run() -> None:
    await init_database()
    while True:
        job_id = await claim()
        if job_id is None:
            await asyncio.sleep(settings.worker_poll_seconds); continue
        try:
            await process(job_id)
        except Exception as exc:
            async with SessionLocal() as session:
                job = await session.get(OutboxJob, job_id)
                if job:
                    job.last_error = str(exc)[:4000]; job.locked_at = None; job.locked_by = None
                    if job.attempts >= job.max_attempts: job.status = "dead"; job.finished_at = utcnow()
                    else: job.status = "retry"; job.available_at = utcnow() + timedelta(seconds=min(300, 2 ** job.attempts))
                    await session.commit()

if __name__ == "__main__": asyncio.run(run())
