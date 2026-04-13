class PipelineError(Exception):
    """Pipeline 基础异常"""


class PreconditionError(PipelineError):
    """Stage 前置条件不满足"""


class StageNotFoundError(PipelineError):
    """Stage 未注册"""


class ProviderNotFoundError(PipelineError):
    """Provider 未注册"""


class StrategyNotFoundError(PipelineError):
    """Strategy 未注册"""


class PipelineAbortedError(PipelineError):
    """Hook 主动终止 Pipeline"""


class QuotaExceededError(PipelineError):
    """租户配额超限"""


class InvalidStateTransitionError(PipelineError):
    """非法状态转换"""
