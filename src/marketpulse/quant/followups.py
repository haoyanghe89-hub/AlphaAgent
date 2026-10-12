"""Bounded followups reuse checked original inputs; unsupported assumptions never disappear."""

import re

from sqlalchemy import select

from marketpulse.investigation.persistence.models import AuditEventRow, InvestigationRunRow

from .contracts import digest


class FollowupError(ValueError):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def request_id(investigation_id, key):
    return "QFOLLOW-" + digest((investigation_id, key))


def existing_request(session, investigation_id, payload, parent_id):
    event = session.get(AuditEventRow, request_id(investigation_id, str(payload.request_key)))
    if event:
        if event.metadata_payload["parent_run_id"] != parent_id or event.metadata_payload[
            "request_hash"
        ] != digest(payload.model_dump(mode="json")):
            raise FollowupError(
                409, "QUANT_REQUEST_CONFLICT", "同一请求标识对应了不同追问，请重新提交。"
            )
        child = session.get(InvestigationRunRow, event.metadata_payload["child_run_id"])
        return dict(run_id=child.run_id, investigation_id=investigation_id, status=child.status)
    return None


def prepare_followup(runtime, parent_id, payload):
    from .api import QuantRunIn

    with runtime.service.sessions() as session:
        parent = session.get(InvestigationRunRow, parent_id)
        if not parent or parent.workflow_version != "quant-v1":
            raise FollowupError(404, "RUN_NOT_FOUND", "原量化任务不存在。")
        previous = existing_request(session, parent.investigation_id, payload, parent_id)
        if previous:
            return previous, None
        if parent.status != "COMPLETED":
            raise FollowupError(409, "QUANT_PARENT_UNFINISHED", "原研究尚未结束，请完成后再追问。")
        event = session.get(AuditEventRow, "QPLAN-" + parent_id)
        if not event:
            raise FollowupError(
                422, "QUANT_PARENT_INPUTS_MISSING", "原任务没有可恢复的冻结研究计划。"
            )
        plan = QuantRunIn.model_validate(
            {k: v for k, v in event.metadata_payload.items() if k in QuantRunIn.model_fields}
        )
    question = payload.question.strip()
    overrides = {}
    if payload.dcf is not None:
        if question != "DCF 按当前假设重算":
            raise FollowupError(
                422, "QUANT_DCF_QUESTION_MISMATCH", "DCF 参数只接受明确的 DCF 重算请求。"
            )
        overrides["dcf"] = payload.dcf
    elif re.fullmatch(r"(?:按)?(?:未复权|前复权|后复权)(?:口径)?(?:重新计算|重算|再算)", question):
        overrides["adjustment"] = (
            "qfq" if "前复权" in question else "hfq" if "后复权" in question else "raw"
        )
    elif re.fullmatch(
        r"无风险利率(?:按|设为|取)?\s*0(?:[.。]0)?\s*[%％]?(?:重新计算|重算|再算)", question
    ):
        overrides["rf"] = "zero"
    elif not re.fullmatch(
        r"(?:重新|再次|再)?(?:核对|复算|查看)(?:原区间)?"
        r"(?:收益(?:与|和)?风险|收益|风险|财务(?:与|和)?估值|估值|财务|原报告|报告)(?:与证据)?",
        question,
    ):
        raise FollowupError(
            422,
            "QUANT_FOLLOWUP_UNSUPPORTED",
            "当前追问支持“重新核对收益与风险”“按前复权重算”或“无风险利率按0重算”；"
            "DCF 请使用报告中的参数滑杆；现金流细分、换区间与多股比较尚未接入。原报告保留。",
        )
    try:
        data = runtime.presentation(parent_id)
        if payload.dcf is not None and not any(
            v["name"] == "dcf_per_share" and v["value"] is not None for v in data.get("values", [])
        ):
            raise FollowupError(
                422,
                "QUANT_DCF_INPUTS_MISSING",
                "原研究缺少适用的官方 DCF 输入，不能用假设补齐现金流。",
            )
        ids = tuple(data.get("input_snapshot_ids", ()))
        if not ids:
            raise FollowupError(
                422,
                "QUANT_PARENT_INPUTS_MISSING",
                "原任务缺少可用冻结输入，不能创建复算版本。请发起新研究。",
            )
        child = plan.model_copy(
            update={"question": question, "frozen_snapshot_ids": ids, **overrides}
        )
        runtime.validate_pins(child)
    except (KeyError, PermissionError, ValueError) as error:
        if isinstance(error, FollowupError):
            raise
        raise FollowupError(
            422,
            "QUANT_FOLLOWUP_INPUT_INVALID",
            "原冻结输入完整性检查失败，或没有请求的复权价格口径。不能用最新数据替换。",
        ) from error
    changes = {
        key: {
            "before": plan.model_dump(mode="json")[key],
            "after": child.model_dump(mode="json")[key],
        }
        for key, value in overrides.items()
        if value != getattr(plan, key)
    }
    return dict(investigation_id=parent.investigation_id, changes=changes), child


def versions(session, investigation_id):
    from marketpulse.investigation.persistence.models import ReportRow

    rows = session.execute(
        select(ReportRow, InvestigationRunRow)
        .join(InvestigationRunRow, ReportRow.run_id == InvestigationRunRow.run_id)
        .where(ReportRow.investigation_id == investigation_id)
        .order_by(ReportRow.version)
    ).all()
    return [
        dict(
            version=report.version,
            run_id=run.run_id,
            report_id=report.report_id,
            parent_run_id=run.origin_run_id,
            created_at=report.created_at.isoformat(),
        )
        for report, run in rows
    ]
