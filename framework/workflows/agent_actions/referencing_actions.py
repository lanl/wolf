from typing import List, Literal
from pydantic import BaseModel, Field, RootModel
from framework.workflows.base_agent_action import AgentAction

# --------------
# Reference object models
# --------------
class AudioReference(BaseModel):
    name: str = Field(description="Name of audio as mentioned in message")
    reference: str = Field(description="uri or path to the audio file")

class ImageReference(BaseModel):
    name: str = Field(description="Name of image as mentioned in message")
    reference: str = Field(description="uri or path to the image file")

class VideoReference(BaseModel):
    name: str = Field(description="Name of video as mentioned in message")
    reference: str = Field(description="uri or path to the video file")

class FileReference(BaseModel):
    name: str = Field(description="Name of file as mentioned in message")
    reference: str = Field(description="uri or path to the file")
# --------------
# Reference actions (audio / image / video / file)
# --------------
