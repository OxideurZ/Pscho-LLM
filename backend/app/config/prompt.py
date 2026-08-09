from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class LoadedPrompt:
    id: str
    version: str
    content: str
    sha256: str


def load_prompt(root: Path, prompt_id: str, version: str) -> LoadedPrompt:
    path = root / "prompts" / prompt_id / f"v{version}.md"
    content = path.read_text(encoding="utf-8")
    return LoadedPrompt(
        id=prompt_id,
        version=version,
        content=content,
        sha256=sha256(content.encode("utf-8")).hexdigest(),
    )
