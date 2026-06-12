from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field

from framework.workflows.base_agent_action import AgentAction
from framework.workflows.agent_actions.referencing_actions import AudioReference, ImageReference, VideoReference, FileReference

# --------------
# Messaging / Notification actions
# --------------
class MessagingActionArgs(BaseModel):
    message: str = Field(description="Content of the message")
    sender: str = Field(description="Sender of the message (very likely you)")
    receiver: str = Field(description="Receiver of the message")
    audio_references: Optional[List[AudioReference]] = Field(default=None, description="List of references for all audio files mentioned in 'message'")
    image_references: Optional[List[ImageReference]] = Field(default=None, description="List of references for all image files mentioned in 'message'")
    video_references: Optional[List[VideoReference]] = Field(default=None, description="List of references for all video files mentioned in 'message'")
    file_references:  Optional[List[FileReference]]  = Field(default=[],description="List of references for all non-(audio, video, or image) files mentioned in 'message'")

class MessagingAction(AgentAction):
    action: Literal["send_message"] = "send_message"
    description: Literal["Action for sending a message"] = "Action for sending a message"
    payload: MessagingActionArgs
    payload_schema: str = """
    {"message": <string>: "Content of the message",
     "sender": <string>: "Content of the message",
     "receiver": <string>: "Content of the message",
     "audio_references": Optional[List[dict]]: [{"name": "name/of/audio/file","path":"path/of/audio/file"}, ...],
     "image_references": Optional[List[dict]]: [{"name": "name/of/image/file","path":"path/of/image/file"}, ...],
     "video_references": Optional[List[dict]]: [{"name": "name/of/video/file","path":"path/of/video/file"}, ...],
     "file_references" : Optional[List[dict]]: [{"name": "name/of/file", "path":"path/of/file"}, ...],
     }
     """
    #yield_motion_to: Optional[str]  = Field(default="entity(user, agent.worker...) who's turn is next",
    #                                        description="entity(user, agent.worker...) who's turn is next")
