"""
RawContent dataclass and BaseCollector ABC.
Every collector returns a list[RawContent] from its collect() method.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawContent:
    source_id:    int
    external_id:  str           # unique ID from the source (post id, tweet id, guid…)
    niche:        str           # 'rocketleague' | 'geometrydash'
    content_type: str           # matches a template key in formatter/templates.py
    title:        str = ""
    url:          str = ""
    body:         str = ""
    image_url:    str = ""
    author:       str = ""
    score:        int = 0       # upvotes, view count, etc. (0 if not applicable)
    metadata:     dict[str, Any] = field(default_factory=dict)


class BaseCollector(ABC):
    def __init__(self, source_id: int, config: dict):
        self.source_id = source_id
        self.config = config

    @abstractmethod
    async def collect(self) -> list[RawContent]:
        """Fetch new items from this source. Returns a list of RawContent."""
        ...
