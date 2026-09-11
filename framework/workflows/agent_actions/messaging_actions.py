from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from framework.workflows.base_agent_action import AgentAction
from framework.workflows.agent_actions.referencing_actions import AudioReference, ImageReference, VideoReference, FileReference
from framework.workflows.agent_actions.formatting_utils import resolve_text_source

# --------------
# Messaging / Notification actions
# --------------
class MessagingActionArgs(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "Status update:\n- Work completed.",
                    "sender": "assistant",
                    "receiver": "user",
                    "file_references": [],
                }
            ]
        }
    )

    message: Optional[str] = Field(default=None, description="Content of the message")
    message_lines: Optional[List[str]] = Field(default=None, description="Safer multiline message transport; joined with newline characters")
    message_base64: Optional[str] = Field(default=None, description="Base64 encoded UTF-8 message")
    sender: str = Field(description="Sender of the message (very likely you)")
    receiver: str = Field(description="Receiver of the message")
    audio_references: Optional[List[AudioReference]] = Field(default=None, description="List of references for all audio files mentioned in 'message'")
    image_references: Optional[List[ImageReference]] = Field(default=None, description="List of references for all image files mentioned in 'message'")
    video_references: Optional[List[VideoReference]] = Field(default=None, description="List of references for all video files mentioned in 'message'")
    file_references:  Optional[List[FileReference]]  = Field(default=[],description="List of references for all non-(audio, video, or image) files mentioned in 'message'")

    @model_validator(mode="after")
    def normalize_message_transport(self):
        self.message = resolve_text_source(
            text=self.message,
            lines=self.message_lines,
            base64_text=self.message_base64,
            field_label="message",
            required=True,
        )
        self.message_lines = None
        self.message_base64 = None
        return self

class MessagingAction(AgentAction):
    action: Literal["send_message"] = "send_message"
    description: Literal["Action for sending a message"] = "Action for sending a message"
    payload: MessagingActionArgs
    payload_schema: str = """
    Preferred user-facing payload:
    {"message": "Content of the message", "sender": "assistant", "receiver": "user", "file_references": []}

    Robust fallback payload for multiline/escape-heavy messages:
    {"message_lines": ["line 1", "line 2"], "sender": "assistant", "receiver": "user"}
    {"message_base64": "base64-encoded UTF-8 message", "sender": "assistant", "receiver": "user"}

    Reference fields:
     "audio_references": Optional[List[dict]]: [{"name": "name/of/audio/file","path":"path/of/audio/file"}, ...],
     "image_references": Optional[List[dict]]: [{"name": "name/of/image/file","path":"path/of/image/file"}, ...],
     "video_references": Optional[List[dict]]: [{"name": "name/of/video/file","path":"path/of/video/file"}, ...],
     "file_references" : Optional[List[dict]]: [{"name": "name/of/file", "path":"path/of/file"}, ...],
     }
     """
    #yield_motion_to: Optional[str]  = Field(default="entity(user, agent.worker...) who's turn is next",
    #                                        description="entity(user, agent.worker...) who's turn is next")
