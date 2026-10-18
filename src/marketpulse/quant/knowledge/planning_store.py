"""Independent immutable investment-plan namespace on the shared archive."""

import copy
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import digest
from ..decision.store import DecisionStore
from ..live.store import ResearchRunRow
from .planning_contracts import PlanRequest


class PlanningStore(DecisionStore):
    prefix = "RUN-IP-"
    request_type = PlanRequest
    schema_version = "investment-plan-v1"
    scenario = "investment"

    def create(self, request):
        run_id = self.prefix + UUID(request["request_key"]).hex
        state = dict(
            schema_version=self.schema_version,
            run_id=run_id,
            request=request,
            status="RUNNING",
            scenario=self.scenario,
            title="、".join(request["entities"]) + " · 投资计划",
            created_at=datetime.now(UTC).isoformat(),
            calls=[],
            process=[],
            tool_results={},
        )
        try:
            with self.base.sessions.begin() as session:
                session.add(
                    ResearchRunRow(
                        run_id=run_id,
                        status=state["status"],
                        created_at=state["created_at"],
                        payload=state,
                    )
                )
                self.register_in_session(session, state)
            return state, True
        except IntegrityError:
            old = self.get(run_id)
            if self.request_type.model_validate(old["request"]) != self.request_type.model_validate(
                request
            ):
                raise ValueError("request identity conflict") from None
            return old, False

    def register_in_session(self, session, state):
        """Subclasses may atomically register a new forward study with its request."""

    def get(self, run_id, *, with_data=True):
        if not re.fullmatch(re.escape(self.prefix) + r"[0-9a-f]{32}", run_id) or not self.exists(
            run_id
        ):
            raise KeyError(run_id)
        state = self.base.get(run_id, with_data=with_data)
        if state.get("result_ref") and with_data:
            if BlobRef.from_uri(state["result_ref"]).sha256 != state["result_sha256"]:
                raise ValueError("plan result hash mismatch")
            data = state["data"]
            if (
                self.request_type.model_validate(data["request"])
                != self.request_type.model_validate(state["request"])
                or digest(self.value(state["inputs_ref"])) != data["input_digest"]
            ):
                raise ValueError("plan request/input identity mismatch")
            if self.value(state["features_ref"]) != data["features"]:
                raise ValueError("feature record mismatch")
        return copy.deepcopy(state)

    def list(self):
        return self.base.list(prefix=self.prefix)
