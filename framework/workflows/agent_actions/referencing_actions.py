from typing import Any
from pydantic import BaseModel, Field, model_validator


# --------------
# Reference object models
# --------------
class _ReferenceBase(BaseModel):
    name: str = Field(description="Name of referenced item as mentioned in message")
    reference: str = Field(description="URI or path to the referenced item")

    @model_validator(mode="before")
    @classmethod
    def accept_path_alias(cls, data: Any):
        """Accept legacy/model-facing path/url aliases for reference.

        Several prompts and GUI/capture paths advertise reference payloads as
        {name, path}, while the canonical Pydantic model field is {name,
        reference}.  Normalize before validation so send_message can attach GUI
        capture files without failing when the model emits path.
        """
        if isinstance(data, dict):
            data = dict(data)
            if "reference" not in data:
                if "path" in data:
                    data["reference"] = data.pop("path")
                elif "url" in data:
                    data["reference"] = data.pop("url")
                elif "uri" in data:
                    data["reference"] = data.pop("uri")
            if "name" not in data and isinstance(data.get("reference"), str):
                data["name"] = data["reference"].rstrip("/").split("/")[-1] or data["reference"]
        return data


class AudioReference(_ReferenceBase):
    name: str = Field(description="Name of audio as mentioned in message")
    reference: str = Field(description="URI or path to the audio file")


class ImageReference(_ReferenceBase):
    name: str = Field(description="Name of image as mentioned in message")
    reference: str = Field(description="URI or path to the image file")


class VideoReference(_ReferenceBase):
    name: str = Field(description="Name of video as mentioned in message")
    reference: str = Field(description="URI or path to the video file")


class FileReference(_ReferenceBase):
    name: str = Field(description="Name of file as mentioned in message")
    reference: str = Field(description="URI or path to the file")


# --------------
# Reference actions (audio / image / video / file)
# --------------
