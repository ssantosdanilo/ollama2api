from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field
import time


class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict]]


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: Optional[str] = None


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: Optional[str] = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[Choice]
    usage: UsageInfo = UsageInfo()


class DeltaMessage(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class ChunkChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChunkChoice]


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "ollama"


class ModelList(BaseModel):
    object: str = "list"
    data: List[ModelInfo]
