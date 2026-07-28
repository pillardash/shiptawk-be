from app.modules.llm.models.generation_run import generation_runs
from app.modules.llm.models.llm_execution import LLMExecution
from app.modules.llm.models.llm_execution_attempt import LLMExecutionAttempt

llm_executions = LLMExecution.__table__
llm_execution_attempts = LLMExecutionAttempt.__table__

__all__ = ["generation_runs", "llm_execution_attempts", "llm_executions"]
