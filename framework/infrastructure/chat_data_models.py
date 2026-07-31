from pydantic import BaseModel, Field

class ChatEntry(BaseModel):
    sender   : str = Field(description="Entry posting the entry in the chat histrory")
    timestamp: str = Field(description="Time stamp the chat entry")
    content  : str = Field(description="Content of the chat entry")  
