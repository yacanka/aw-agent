from __future__ import annotations

import json
import re
import uuid
from typing import Any

GEMMA_STRING_DELIMITER = '<|"|>'


class GemmaParseError(ValueError):
    pass


class GemmaArgumentParser:
    """
    Gemma agentic tool-call argüman formatını parse eder.

    Örnekler:
        {directory:<|"|>.<|"|>}
        {path:<|"|>hello.py<|"|>}
        {content:<|"|>abc<|"|>,path:<|"|>hello.py<|"|>}
        {recursive:true,count:10}
        {items:[1,2,3]}
        {options:{recursive:true}}
    """

    def __init__(self, text: str):
        self.text = text
        self.i = 0

    def _skip_ws(self) -> None:
        while self.i < len(self.text) and self.text[self.i].isspace():
            self.i += 1

    def _peek(self) -> str | None:
        self._skip_ws()
        if self.i >= len(self.text):
            return None
        return self.text[self.i]

    def parse(self) -> Any:
        self._skip_ws()
        value = self._parse_value()
        self._skip_ws()

        if self.i != len(self.text):
            raise GemmaParseError(
                "Unexpected trailing content near: "
                + repr(self.text[self.i : self.i + 80])
            )

        return value

    def _parse_value(self) -> Any:
        self._skip_ws()

        if self.text.startswith(GEMMA_STRING_DELIMITER, self.i):
            return self._parse_gemma_string()

        ch = self._peek()

        if ch is None:
            raise GemmaParseError("Unexpected end of arguments")

        if ch == "{":
            return self._parse_object()

        if ch == "[":
            return self._parse_array()

        if ch == '"':
            return self._parse_json_string()

        return self._parse_bare_value()

    def _parse_gemma_string(self) -> str:
        self.i += len(GEMMA_STRING_DELIMITER)

        end = self.text.find(
            GEMMA_STRING_DELIMITER,
            self.i,
        )

        if end == -1:
            raise GemmaParseError("Unterminated Gemma string")

        value = self.text[self.i : end]
        self.i = end + len(GEMMA_STRING_DELIMITER)
        return value

    def _parse_json_string(self) -> str:
        decoder = json.JSONDecoder()

        try:
            value, consumed = decoder.raw_decode(self.text[self.i :])
        except json.JSONDecodeError as exc:
            raise GemmaParseError(str(exc)) from exc

        self.i += consumed

        if not isinstance(value, str):
            raise GemmaParseError("Expected JSON string")

        return value

    def _parse_key(self) -> str:
        self._skip_ws()

        if self._peek() == '"':
            return self._parse_json_string()

        match = re.match(
            r"[A-Za-z_][A-Za-z0-9_]*",
            self.text[self.i :],
        )

        if not match:
            raise GemmaParseError(
                "Invalid object key near: "
                + repr(self.text[self.i : self.i + 80])
            )

        key = match.group(0)
        self.i += len(key)
        return key

    def _parse_object(self) -> dict[str, Any]:
        result: dict[str, Any] = {}

        if self._peek() != "{":
            raise GemmaParseError("Expected '{'")

        self.i += 1

        while True:
            self._skip_ws()

            if self._peek() == "}":
                self.i += 1
                return result

            key = self._parse_key()
            self._skip_ws()

            if self._peek() != ":":
                raise GemmaParseError(
                    f"Expected ':' after key {key!r}"
                )

            self.i += 1
            result[key] = self._parse_value()
            self._skip_ws()

            ch = self._peek()

            if ch == ",":
                self.i += 1
                continue

            if ch == "}":
                self.i += 1
                return result

            raise GemmaParseError(
                "Expected ',' or '}' near: "
                + repr(self.text[self.i : self.i + 80])
            )

    def _parse_array(self) -> list[Any]:
        result: list[Any] = []

        if self._peek() != "[":
            raise GemmaParseError("Expected '['")

        self.i += 1

        while True:
            self._skip_ws()

            if self._peek() == "]":
                self.i += 1
                return result

            result.append(self._parse_value())
            self._skip_ws()

            ch = self._peek()

            if ch == ",":
                self.i += 1
                continue

            if ch == "]":
                self.i += 1
                return result

            raise GemmaParseError(
                "Expected ',' or ']' near: "
                + repr(self.text[self.i : self.i + 80])
            )

    def _parse_bare_value(self) -> Any:
        self._skip_ws()
        start = self.i

        while self.i < len(self.text):
            if self.text[self.i] in ",}]":
                break
            self.i += 1

        token = self.text[start : self.i].strip()

        if not token:
            raise GemmaParseError("Empty bare value")

        lowered = token.lower()

        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered == "null":
            return None

        try:
            return int(token)
        except ValueError:
            pass

        try:
            return float(token)
        except ValueError:
            pass

        return token


def _argument_candidates(raw: str) -> list[str]:
    """
    Model bazen geçerli argümanı gereksiz bir ekstra {} katmanına sarar:

        {{"command": "python hello.py"}}

    Bu fonksiyon hem orijinal metni hem de güvenli biçimde birer dış
    süslü parantez katmanı kaldırılmış adayları üretir.
    """
    candidates = [raw]
    current = raw

    # Birkaç katmandan fazlasını açmaya gerek yok.
    for _ in range(3):
        current = current.strip()

        if not (
            current.startswith("{{")
            and current.endswith("}}")
        ):
            break

        current = current[1:-1].strip()

        if current not in candidates:
            candidates.append(current)

    return candidates


def parse_gemma_arguments(raw: str) -> dict[str, Any]:
    raw = raw.strip()

    if not raw:
        return {}

    errors: list[str] = []

    for candidate in _argument_candidates(raw):
        # 1) Standart JSON
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(
                f"JSON({candidate[:80]!r}): {exc}"
            )
        else:
            if isinstance(parsed, dict):
                return parsed

            errors.append(
                "JSON parsed successfully but result was not an object"
            )
            continue

        # 2) Gemma özel syntax
        try:
            parsed = GemmaArgumentParser(candidate).parse()
        except GemmaParseError as exc:
            errors.append(
                f"Gemma({candidate[:80]!r}): {exc}"
            )
            continue

        if isinstance(parsed, dict):
            return parsed

        errors.append(
            "Gemma parser result was not an object"
        )

    raise GemmaParseError(
        "Could not parse tool arguments. "
        + " | ".join(errors[-4:])
    )


def extract_gemma_thoughts(content: str) -> list[str]:
    """
    <|channel>thought ... <channel|> bloklarını döndürür.
    """
    if not content:
        return []

    pattern = re.compile(
        r"<\|channel>thought\s*(.*?)<channel\|>",
        re.DOTALL,
    )

    return [
        match.strip()
        for match in pattern.findall(content)
        if match.strip()
    ]


def parse_gemma_tool_calls(content: str) -> list[dict[str, Any]]:
    """
    Gemma native tool-call çıktısını OpenAI-benzeri tool_calls listesine çevirir.

    Örnek:
        <|tool_call>call:list_files{directory:<|"|>.<|"|>}<tool_call|>
    """
    if not content:
        return []

    pattern = re.compile(
        r"<\|tool_call>(.*?)(?:<tool_call\|>|$)",
        re.DOTALL,
    )

    calls: list[dict[str, Any]] = []

    for block in pattern.findall(content):
        block = block.strip()

        match = re.search(
            r"call\s*:\s*([A-Za-z_][A-Za-z0-9_]*)",
            block,
        )

        if not match:
            continue

        function_name = match.group(1)
        raw_arguments = block[match.end() :].strip()

        arguments = parse_gemma_arguments(raw_arguments)

        calls.append(
            {
                "id": "call_" + uuid.uuid4().hex[:12],
                "type": "function",
                "function": {
                    "name": function_name,
                    "arguments": json.dumps(
                        arguments,
                        ensure_ascii=False,
                    ),
                },
            }
        )

    return calls


def extract_visible_content(content: str) -> str:
    """
    Thought/tool bloklarını final kullanıcı cevabından temizler.
    """
    if not content:
        return ""

    text = re.sub(
        r"<\|channel>thought\s*.*?<channel\|>",
        "",
        content,
        flags=re.DOTALL,
    )

    text = re.sub(
        r"<\|tool_call>.*?(?:<tool_call\|>|$)",
        "",
        text,
        flags=re.DOTALL,
    )

    # Muhtemel kalan channel markerlarını temizle.
    text = re.sub(
        r"<\|channel>[A-Za-z0-9_-]+\s*",
        "",
        text,
    )
    text = text.replace("<channel|>", "")

    return text.strip()
