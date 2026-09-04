from app.contexts.jd_lifecycle._applications.jd_common import (
    TASK_INTERNAL_ROLES,
    _forbidden,
    _not_found,
)
from app.domain.jd_policies import JDParseCommand
from app.contexts.jd_lifecycle._applications.jd_support import require_optional_port_result, require_port_result
from app.contexts.jd_lifecycle._ports.jd_repository import (
    Actor,
    JDParseBatch,
    JDParseResultDTO,
    JDSchemaView,
    TaskDTO,
)


class JDParsingUseCases:
    def parse_batch(
        self, actor: Actor, jd_ids: list[str], extraction_mode: str
    ) -> JDParseBatch:
        with self._uow_factory() as uow:
            results = []
            for jd_id in jd_ids:
                jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
                results.append(self._parse_jd(uow, actor, jd, extraction_mode))
            uow.commit()
            return JDParseBatch(tuple(results))

    def parse(
        self, actor: Actor, jd_id: str, command: JDParseCommand
    ) -> TaskDTO:
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=True)
            result = self._parse_jd(uow, actor, jd, command.extraction_mode)
            task = require_port_result(
                uow.tasks.create_succeeded_parse_task(
                    actor,
                    command,
                    result,
                    require_port_result(
                        self._schema.view(
                            result.extraction_result,
                            result.normalized_result,
                            schema_version=result.schema_version,
                            normalization_schema_version=(
                                result.normalization_schema_version
                            ),
                        ),
                        JDSchemaView,
                        operation="JDSchemaPort.view",
                    ),
                ),
                TaskDTO,
                operation="TaskRepository.create_succeeded_parse_task",
            )
            uow.commit()
            return task

    def get_parse_task(self, actor: Actor, task_id: str) -> TaskDTO:
        with self._uow_factory() as uow:
            task = require_optional_port_result(
                uow.tasks.get_task(task_id),
                TaskDTO,
                operation="TaskRepository.get_task",
            )
            if task is None or task.task_type != "jd_parse":
                raise _not_found("Task not found")
            if task.created_by != actor.id and actor.role not in TASK_INTERNAL_ROLES:
                raise _forbidden("No permission to access this task")
            return task

    def get_parse_result(self, actor: Actor, jd_id: str) -> JDParseResultDTO:
        with self._uow_factory() as uow:
            jd = self._get_accessible_jd(uow, actor, jd_id, write=False)
            return self._get_parse_result(uow, jd.id)
