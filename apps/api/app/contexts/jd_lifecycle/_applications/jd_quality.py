from app.contexts.jd_lifecycle._applications.jd_common import (
    _conflict,
    _ensure_can_admin,
)
from app.contexts.jd_lifecycle._applications.jd_support import (
    inflation_facts_from_result,
    require_optional_port_result,
    require_port_list,
    require_port_result,
)
from app.domain.jd_policies import (
    duplicate_action,
    evaluate_inflation,
    inflation_action,
)
from app.contexts.jd_lifecycle._ports.jd_repository import (
    Actor,
    DuplicateCheckBatch,
    DuplicateCheckResult,
    InflationCheckBatch,
    InflationCheckResult,
    JDDTO,
    JDDownweightUpdate,
    JDParseResultDTO,
    SimilarJD,
)


class JDQualityUseCases:
    def duplicate_check(self, actor: Actor, jd_id: str) -> DuplicateCheckResult:
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
            result = self._duplicate_check(uow, jd)
            uow.commit()
            return result

    def duplicate_check_batch(
        self, actor: Actor, jd_ids: list[str]
    ) -> DuplicateCheckBatch:
        _ensure_can_admin(actor)
        with self._uow_factory() as uow:
            ids = jd_ids or [
                jd.id for jd in require_port_list(
                    uow.jds.list_jds(), JDDTO, operation="JDRepository.list_jds"
                )
            ]
            items = []
            for jd_id in ids:
                jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
                items.append(self._duplicate_check(uow, jd))
            uow.commit()
            return DuplicateCheckBatch(tuple(items))

    def similar_jds(self, actor: Actor, jd_id: str) -> tuple[SimilarJD, ...]:
        return self.duplicate_check(actor, jd_id).similar_jds

    def copy_risk_report(self, actor: Actor, jd_id: str) -> DuplicateCheckResult:
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=False)
            if jd.copy_risk_score is None:
                result = self._duplicate_check(uow, jd)
                uow.commit()
                return result
            return DuplicateCheckResult(
                jd.id,
                jd.copy_risk_score,
                (),
                duplicate_action(jd.copy_risk_score),
                "已保存的 mock 抄袭风险分数",
            )

    def inflation_check(self, actor: Actor, jd_id: str) -> InflationCheckResult:
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
            response = self._inflation_check_for_jd(uow, actor, jd)
            uow.commit()
            return response

    def inflation_check_batch(
        self, actor: Actor, jd_ids: list[str]
    ) -> InflationCheckBatch:
        _ensure_can_admin(actor)
        with self._uow_factory() as uow:
            ids = jd_ids or [
                jd.id for jd in require_port_list(
                    uow.jds.list_jds(), JDDTO, operation="JDRepository.list_jds"
                )
            ]
            items = []
            for jd_id in ids:
                jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
                items.append(self._inflation_check_for_jd(uow, actor, jd))
            uow.commit()
            return InflationCheckBatch(tuple(items))

    def inflation_report(self, actor: Actor, jd_id: str) -> InflationCheckResult:
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=False)
            if jd.inflation_score is None:
                result = require_optional_port_result(
                    uow.jds.get_parse_result(jd.id),
                    JDParseResultDTO,
                    operation="JDRepository.get_parse_result",
                )
                if result is None:
                    raise _conflict(
                        "Parse the JD with an explicit extraction_mode first"
                    )
                response = self._inflation_check(uow, jd, result)
                uow.commit()
                return response
            result = require_optional_port_result(
                uow.jds.get_parse_result(jd.id),
                JDParseResultDTO,
                operation="JDRepository.get_parse_result",
            )
            reasons = (
                evaluate_inflation(
                    inflation_facts_from_result(jd, result)
                ).mismatch_reasons
                if result is not None
                else ()
            )
            return InflationCheckResult(
                jd.id,
                jd.inflation_score,
                (),
                inflation_action(jd.inflation_score),
                reasons,
            )

    def downweight_jd(self, actor: Actor, jd_id: str) -> JDDTO:
        _ensure_can_admin(actor)
        with self._uow_factory() as uow:
            self._get_accessible_jd(uow, actor, jd_id, write=True)
            jd = require_port_result(
                uow.jds.update_jd(jd_id, JDDownweightUpdate(True)),
                JDDTO,
                operation="JDRepository.update_jd",
            )
            uow.commit()
            return jd
