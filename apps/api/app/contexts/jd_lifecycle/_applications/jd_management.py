from app.contexts.jd_lifecycle._applications.jd_common import (
    JDFileCreateCommand,
    JDTextCreateCommand,
    _ensure_can_admin,
    _ensure_can_create,
    _ensure_can_read,
    _ensure_can_write,
    _conflict,
    _invalid,
)
from app.contexts.jd_lifecycle._applications.jd_support import (
    require_optional_port_result,
    require_port_list,
    require_port_result,
)
from app.contexts.jd_lifecycle._ports.jd_repository import (
    Actor,
    FileAssetDTO,
    FileTextExtractionResult,
    JDBatch,
    JDCreateCommand,
    JDCreated,
    JDDTO,
    JDSummaryDTO,
    JDParseResultDTO,
    JDParseResultResetUpdate,
    JDRawTextUpdate,
)
from app.domain.jd_policies import JDPolicyViolation, validate_jd_raw_text


def _validate_raw_text(raw_text: str) -> None:
    try:
        validate_jd_raw_text(raw_text)
    except JDPolicyViolation as exc:
        raise _invalid(str(exc)) from exc


class JDManagementUseCases:
    def create_text(self, actor: Actor, command: JDTextCreateCommand) -> JDCreated:
        _ensure_can_create(actor)
        _validate_raw_text(command.raw_text)
        with self._uow_factory() as uow:
            enterprise_id = self._resolve_enterprise_id(uow, actor, command.enterprise_id)
            jd = require_port_result(
                uow.jds.create_jd(JDCreateCommand(
                    source_type=command.source_type,
                    source_name=command.source_name,
                    enterprise_id=enterprise_id,
                    title=command.title,
                    raw_text=command.raw_text,
                    cleaned_text=command.cleaned_text,
                    publish_date=command.publish_date,
                    url=command.url,
                    input_extraction_status="not_required",
                    input_provider="direct_text",
                )),
                JDDTO,
                operation="JDRepository.create_jd",
            )
            uow.commit()
            return JDCreated(jd.id, jd.parse_status, jd.created_at)

    def create_file(self, actor: Actor, command: JDFileCreateCommand) -> JDDTO:
        _ensure_can_create(actor)
        with self._uow_factory() as uow:
            enterprise_id = self._resolve_enterprise_id(uow, actor, command.enterprise_id)
            try:
                file_asset = require_port_result(
                    uow.files.save_upload(
                        actor,
                        command.upload,
                        purpose="jd_image" if command.use_ocr else "jd_file",
                    ),
                    FileAssetDTO,
                    operation="FileRepository.save_upload",
                )
            except ValueError as exc:
                raise _invalid(str(exc)) from exc
            outcome = require_port_result(
                uow.file_text_extractor.extract_text(
                    file_asset, use_ocr=command.use_ocr
                ),
                FileTextExtractionResult,
                operation="FileTextExtractor.extract_text",
            )
            _validate_raw_text(outcome.text)
            jd = require_port_result(
                uow.jds.create_jd(JDCreateCommand(
                    source_type=command.source_type,
                    source_name=command.source_name or file_asset.filename,
                    enterprise_id=enterprise_id,
                    title=command.title,
                    raw_text=outcome.text,
                    file_id=file_asset.id,
                    parse_status="pending" if outcome.status == "completed" else "failed",
                    input_extraction_status=outcome.status,
                    input_provider=outcome.provider,
                    input_error_code=outcome.error_code,
                    input_error_message=outcome.error_message,
                )),
                JDDTO,
                operation="JDRepository.create_jd",
            )
            uow.commit()
            return jd

    def create_batch(
        self, actor: Actor, commands: list[JDTextCreateCommand]
    ) -> JDBatch:
        _ensure_can_create(actor)
        for command in commands:
            _validate_raw_text(command.raw_text)
        with self._uow_factory() as uow:
            created: list[JDDTO] = []
            for command in commands:
                enterprise_id = self._resolve_enterprise_id(
                    uow, actor, command.enterprise_id
                )
                created.append(
                    require_port_result(
                        uow.jds.create_jd(JDCreateCommand(
                            source_type=command.source_type,
                            source_name=command.source_name,
                            enterprise_id=enterprise_id,
                            title=command.title,
                            raw_text=command.raw_text,
                            cleaned_text=command.cleaned_text,
                            publish_date=command.publish_date,
                            url=command.url,
                            input_extraction_status="not_required",
                            input_provider="direct_text",
                        )),
                        JDDTO,
                        operation="JDRepository.create_jd",
                    )
                )
            uow.commit()
            return JDBatch(tuple(created))

    def list_jds(self, actor: Actor) -> list[JDDTO]:
        _ensure_can_read(actor)
        with self._uow_factory() as uow:
            enterprise_ids = (
                uow.jds.owned_enterprise_ids(actor.id)
                if actor.role == "enterprise_user"
                else None
            )
            return require_port_list(
                uow.jds.list_jds(enterprise_ids),
                JDDTO,
                operation="JDRepository.list_jds",
            )

    def list_jds_page(
        self,
        actor: Actor,
        *,
        offset: int,
        limit: int,
        query: str | None,
        sort: str = "created_desc",
    ) -> tuple[list[JDDTO], int]:
        if sort not in {"created_desc", "created_asc", "title_asc"}:
            raise ValueError("Invalid sort")
        _ensure_can_read(actor)
        with self._uow_factory() as uow:
            enterprise_ids = (
                uow.jds.owned_enterprise_ids(actor.id)
                if actor.role == "enterprise_user"
                else None
            )
            items, total = uow.jds.list_jds_page(
                enterprise_ids,
                offset=offset,
                limit=limit,
                query=query,
                sort=sort,
            )
            return (
                require_port_list(
                    items,
                    JDDTO,
                    operation="JDRepository.list_jds_page",
                ),
                total,
            )

    def summarize_jds(self, actor: Actor) -> JDSummaryDTO:
        _ensure_can_read(actor)
        with self._uow_factory() as uow:
            enterprise_ids = (
                uow.jds.owned_enterprise_ids(actor.id)
                if actor.role == "enterprise_user"
                else None
            )
            return uow.jds.summarize_jds(enterprise_ids)

    def get_jd(self, actor: Actor, jd_id: str) -> JDDTO:
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=False)
            return jd

    def update_raw_text(self, actor: Actor, jd_id: str, raw_text: str) -> JDDTO:
        _ensure_can_write(actor)
        _validate_raw_text(raw_text)
        with self._uow_factory() as uow:
            self._get_accessible_jd(uow, actor, jd_id, write=True)
            result = require_optional_port_result(
                uow.jds.get_parse_result(jd_id),
                JDParseResultDTO,
                operation="JDRepository.get_parse_result",
            )
            if result and result.workflow_status == "published":
                raise _conflict(
                    "Published JD results are immutable; create a new JD version"
                )
            jd = require_port_result(uow.jds.update_jd(
                jd_id,
                JDRawTextUpdate(
                    raw_text=raw_text,
                    parse_status="pending",
                    input_extraction_status="manually_edited",
                    input_provider="manual",
                    input_error_code=None,
                    input_error_message=None,
                ),
            ), JDDTO, operation="JDRepository.update_jd")
            if result:
                require_port_result(
                    uow.jds.update_parse_result(
                        result.id,
                        JDParseResultResetUpdate(),
                    ),
                    JDParseResultDTO,
                    operation="JDRepository.update_parse_result",
                )
            uow.commit()
            return jd

    def delete_jd(self, actor: Actor, jd_id: str) -> None:
        _ensure_can_admin(actor)
        with self._uow_factory() as uow:
            self._get_accessible_jd(uow, actor, jd_id, write=True)
            uow.jds.delete_jd(jd_id)
            uow.commit()

    def deprecate_jd(self, actor: Actor, jd_id: str) -> None:
        _ensure_can_admin(actor)
        with self._uow_factory() as uow:
            self._get_accessible_jd(uow, actor, jd_id, write=True)
            uow.jds.deprecate_jd(jd_id)
            uow.commit()
