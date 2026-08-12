import io
import copy
import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


TEMPLATE_FIELDS = {
    "[Date]": "date",
    "[Recipient Name]": "recipient_name",
    "[Title / Company]": "recipient_title_company",
    "[Street Address]": "street_address",
    "[City, State, Postcode]": "city_state_postcode",
    "[Opening paragraph — state the purpose of your letter. Begin here and the body text will flow naturally across as many pages as you need, with the letterhead repeating on each printed sheet.]": "opening_paragraph",
    "[Body paragraph — provide the detail, context, or supporting points. Keep the tone confident, clear, and advisory, in plain language that a non-technical reader can follow.]": "body_paragraph",
    "[Closing paragraph — summarise the next step or call to action and offer to discuss further.]": "closing_paragraph",
    "[Your Name]": "sender_name",
    "[Your Title]": "sender_title",
}

BODY_FIELDS = {"opening_paragraph", "body_paragraph", "closing_paragraph"}


def _markdown_to_blocks(value: str) -> list[str]:
    """Turn occasional LLM Markdown into clean Word-ready text blocks."""
    text = html.unescape(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```(?:\w+)?\s*\n?([\s\S]*?)```", r"\1", text)
    text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)

    blocks: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            if blocks and blocks[-1] != "":
                blocks.append("")
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^>\s?", "", line)
        line = re.sub(r"^[-*_]{3,}$", "", line)
        line = re.sub(r"^[-*+]\s+", "• ", line)
        line = re.sub(r"^(\d+)[.)]\s+", r"\1. ", line)
        line = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m.group(1) or m.group(2), line)
        line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)", lambda m: m.group(1) or m.group(2), line)
        line = re.sub(r"~~([^~]+)~~", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            line = " — ".join(cell for cell in cells if cell)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            blocks.append(line)

    while blocks and blocks[-1] == "":
        blocks.pop()
    # Blank lines are represented by Word paragraph spacing, not empty paragraphs.
    return [block for block in blocks if block] or [""]


def _set_paragraph_text(paragraph, text_tag: str, value: str) -> None:
    nodes = list(paragraph.iter(text_tag))
    if not nodes:
        return
    nodes[0].text = value
    nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for node in nodes[1:]:
        node.text = ""


def _set_compact_spacing(paragraph, word_ns: str) -> None:
    paragraph_properties = paragraph.find(f"{{{word_ns}}}pPr")
    if paragraph_properties is None:
        paragraph_properties = ElementTree.Element(f"{{{word_ns}}}pPr")
        paragraph.insert(0, paragraph_properties)
    spacing = paragraph_properties.find(f"{{{word_ns}}}spacing")
    if spacing is None:
        spacing = ElementTree.SubElement(
            paragraph_properties, f"{{{word_ns}}}spacing"
        )
    spacing.set(f"{{{word_ns}}}after", "220")


def fill_template(template: Path, values: dict[str, str]) -> io.BytesIO:
    """Replace placeholders even when Word has split them across multiple runs."""
    output = io.BytesIO()
    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    text_tag = f"{{{word_ns}}}t"
    paragraph_tag = f"{{{word_ns}}}p"

    with zipfile.ZipFile(template, "r") as source, zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                root = ElementTree.fromstring(data)
                changed = False
                parent_by_child = {
                    child: parent for parent in root.iter() for child in parent
                }
                for paragraph in list(root.iter(paragraph_tag)):
                    nodes = list(paragraph.iter(text_tag))
                    if not nodes:
                        continue
                    full_text = "".join(node.text or "" for node in nodes)

                    body_placeholder = next(
                        (
                            (placeholder, field)
                            for placeholder, field in TEMPLATE_FIELDS.items()
                            if field in BODY_FIELDS and full_text == placeholder
                        ),
                        None,
                    )
                    if body_placeholder:
                        placeholder, field = body_placeholder
                        blocks = _markdown_to_blocks(values.get(field, ""))
                        parent = parent_by_child.get(paragraph)
                        if parent is not None:
                            index = list(parent).index(paragraph)
                            for offset, block in enumerate(blocks):
                                clone = copy.deepcopy(paragraph)
                                _set_paragraph_text(clone, text_tag, block)
                                if offset < len(blocks) - 1:
                                    _set_compact_spacing(clone, word_ns)
                                parent.insert(index + offset, clone)
                            parent.remove(paragraph)
                            changed = True
                            continue

                    replaced = full_text
                    for placeholder, field in TEMPLATE_FIELDS.items():
                        replaced = replaced.replace(placeholder, values.get(field, ""))
                    if replaced != full_text:
                        _set_paragraph_text(paragraph, text_tag, replaced)
                        changed = True
                if changed:
                    data = ElementTree.tostring(
                        root, encoding="utf-8", xml_declaration=True
                    )
            target.writestr(item, data)
    output.seek(0)
    return output
