from app.graph.state import (
    VideoWorkflowState,
    ScriptEvaluationReport,
    VisualEvaluationReport,
    AudioEvaluationReport,
    VideoQAReport,
    EditRequest,
)


def __getattr__(name: str):
    if name == "video_graph":
        try:
            from app.graph.workflow import video_graph
            return video_graph
        except ImportError:
            return None
    if name == "GraphRunner":
        try:
            from app.graph.runner import GraphRunner
            return GraphRunner
        except ImportError:
            return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "VideoWorkflowState",
    "ScriptEvaluationReport",
    "VisualEvaluationReport",
    "AudioEvaluationReport",
    "VideoQAReport",
    "EditRequest",
    "video_graph",
    "GraphRunner",
]
