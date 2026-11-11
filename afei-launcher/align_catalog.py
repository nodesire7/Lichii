# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an external Qt catalog for the exact upstream release commit."""

import argparse
import ast
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from updater import UPSTREAM, official_asset, sha256_file

HERE = Path(__file__).resolve().parent


def add_qt_widget_labels(catalog):
    standard_buttons = (
        ("OK", "确定"), ("Cancel", "取消"), ("Close", "关闭"), ("Apply", "应用"),
        ("&Yes", "是(&Y)"), ("&No", "否(&N)"), ("Help", "帮助"), ("Reset", "重置"),
        ("Open", "打开"), ("Save", "保存"), ("Discard", "丢弃"), ("Retry", "重试"),
        ("Restore Defaults", "恢复默认值"),
    )
    entries = [{"context": "QPlatformTheme", "source": source, "translation": translation}
               for source, translation in standard_buttons]
    entries += json.loads((HERE / "source.qt.zh_CN.json").read_text(encoding="utf-8"))
    template = ET.Element("TS")
    contexts = {}
    for entry in entries:
        name = entry["context"]
        if name not in contexts:
            context = ET.SubElement(template, "context")
            ET.SubElement(context, "name").text = name
            contexts[name] = context
        message = ET.SubElement(contexts[name], "message")
        ET.SubElement(message, "source").text = entry["source"]
        if entry.get("comment"):
            ET.SubElement(message, "comment").text = entry["comment"]
    align(catalog, template, entries)


def revision():
    return hashlib.sha256(b"".join((HERE / name).read_bytes().replace(b"\r\n", b"\n") for name in (
        "align_catalog.py", "overrides.zh_CN.json", "source.zh_CN.json", "source.graphics.zh_CN.json",
        "source.qt.zh_CN.json",
    ))).hexdigest()


def message_key(context, message):
    return (context.findtext("name"), message.findtext("source"), message.findtext("comment") or "")


def messages(root):
    return {message_key(context, message): message
            for context in root.findall("context") for message in context.findall("message")}


def translation_text(message):
    if message is None:
        return ""
    translation = message.find("translation")
    if translation is None or translation.get("type") in {"unfinished", "vanished", "obsolete"}:
        return ""
    return "".join(translation.itertext()).strip()


def reuse_translations(base, template, *references):
    """Copy existing translations only when the full source key and plural mode agree."""
    current = messages(base)
    contexts = {context.findtext("name"): context for context in base.findall("context")}
    references = (current, *references)
    for key, live in messages(template).items():
        if translation_text(current.get(key)):
            continue
        context_name, source, comment = key
        aliases = (key,)
        if context_name.startswith(("FritchiiUi::", "NetworkMessage::")):
            aliases += ((context_name.rsplit("::", 1)[-1], source, comment),)
        candidates = (reference.get(alias) for reference in references for alias in aliases)
        match = next((item for item in candidates if translation_text(item)
                      and item.get("numerus") == live.get("numerus")), None)
        if match is None:
            continue
        context = contexts.get(context_name)
        if context is None:
            context = ET.SubElement(base, "context")
            ET.SubElement(context, "name").text = context_name
            contexts[context_name] = context
        message = current.get(key)
        if message is None:
            message = deepcopy(live)
            context.append(message)
        old = message.find("translation")
        if old is not None:
            message.remove(old)
        message.append(deepcopy(match.find("translation")))


def source_coverage(base, template):
    current = messages(base)
    live = messages(template)
    without_entry = []
    with_chinese = 0
    for (context, source, comment), _ in live.items():
        translated = translation_text(current.get((context, source, comment)))
        if not translated:
            without_entry.append({"context": context, "source": source, "comment": comment})
        elif re.search(r"[\u3400-\u9fff]", translated):
            with_chinese += 1
    return {
        "source_entries": len(live),
        "entries_with_catalog_text": len(live) - len(without_entry),
        "entries_with_chinese_text": with_chinese,
        "without_catalog_text": without_entry,
    }


def add_graphics_source_keys(template, source):
    # These files wrap QCoreApplication::translate in Tr, which lupdate does not recognize.
    literal = r'"(?:[^"\\]|\\.)*"'
    current = messages(template)
    for filename in ("amd_fsr4_panel.cpp", "nvidia_dlss_panel.cpp"):
        path = source / "src/yuzu/configuration" / filename
        if not path.is_file():
            continue
        cpp = path.read_text(encoding="utf-8")
        wrapper = re.search(r'QCoreApplication::translate\("([^"]+)",\s*text\)', cpp)
        if wrapper is None:
            continue
        name = wrapper[1]
        context = next((item for item in template.findall("context")
                        if item.findtext("name") == name), None)
        for match in re.finditer(r"\bTr\(\s*((?:" + literal + r"\s*)+)", cpp):
            text = "".join(ast.literal_eval(part) for part in re.findall(literal, match[1]))
            key = (name, text, "")
            if key in current:
                continue
            if context is None:
                context = ET.SubElement(template, "context")
                ET.SubElement(context, "name").text = name
            message = ET.SubElement(context, "message")
            ET.SubElement(message, "source").text = text
            current[key] = message


def add_runtime_source_keys(template, source):
    path = source / "src/yuzu/configuration/configure_ui.cpp"
    if not path.is_file():
        return
    cpp = path.read_text(encoding="utf-8")
    key = ("QWidget", "Auto", "Screenshot height option")
    if ('QWidget* parent)' in cpp and 'parent->tr("Auto", "Screenshot height option")' in cpp
            and key not in messages(template)):
        context = next((item for item in template.findall("context") if item.findtext("name") == key[0]), None)
        if context is None:
            context = ET.SubElement(template, "context")
            ET.SubElement(context, "name").text = key[0]
        message = ET.SubElement(context, "message")
        ET.SubElement(message, "source").text = key[1]
        ET.SubElement(message, "comment").text = key[2]


def add_runtime_contexts(base, template, source):
    """Copy extracted keys to the Qt classes that actually call translate()."""
    aliases = {}
    cpp_sources = source / "src"
    checks = (
        ("qt_common/qt_common.cpp", "return rootObject->tr(str);",
         tuple(f"QtCommon::{name}" for name in ("Content", "FS", "Game", "Mod", "Path")), "QWidget"),
        ("qt_common/qt_string_lookup.h", "return QObject::tr(strings.at(key).data());",
         ("QtCommon::StringLookup",), "QObject"),
        ("yuzu/multiplayer/message.cpp", "QObject::tr(text.c_str())", ("NetworkMessage",), "QObject"),
        ("yuzu/fritchii/fritchii_ui.cpp", "[](const char* text) { return QObject::tr(text); }",
         ("FritchiiUi",), "QObject"),
    )
    for filename, call, extracted, runtime in checks:
        path = cpp_sources / filename
        if path.is_file() and call in path.read_text(encoding="utf-8"):
            aliases.update(dict.fromkeys(extracted, runtime))
    header = cpp_sources / "yuzu/configuration/system/profile_avatar_dialog.h"
    if header.is_file():
        cpp = header.read_text(encoding="utf-8")
        if ("class ProfileAvatarDialog : public QDialog" in cpp
                and not re.search(r"\b(?:Q_OBJECT|Q_DECLARE_TR_FUNCTIONS)\b", cpp)):
            aliases["ProfileAvatarDialog"] = "QDialog"

    live = messages(template)
    current = messages(base)
    live_contexts = {context.findtext("name"): context for context in template.findall("context")}
    base_contexts = {context.findtext("name"): context for context in base.findall("context")}
    for key, extracted in list(live.items()):
        name = aliases.get(key[0])
        if name is None:
            continue
        runtime_key = (name, *key[1:])
        if runtime_key not in live:
            if name not in live_contexts:
                context = ET.SubElement(template, "context")
                ET.SubElement(context, "name").text = name
                live_contexts[name] = context
            live[runtime_key] = deepcopy(extracted)
            live_contexts[name].append(live[runtime_key])
        donor = current.get(key)
        if translation_text(current.get(runtime_key)) or not translation_text(donor):
            continue
        target = current.get(runtime_key)
        if target is None:
            if name not in base_contexts:
                context = ET.SubElement(base, "context")
                ET.SubElement(context, "name").text = name
                base_contexts[name] = context
            target = deepcopy(extracted)
            base_contexts[name].append(target)
            current[runtime_key] = target
        old = target.find("translation")
        if old is not None:
            target.remove(old)
        target.append(deepcopy(donor.find("translation")))


def align(base, template, overrides):
    active = messages(template)
    current = messages(base)
    contexts = {context.findtext("name"): context for context in base.findall("context")}
    applied, pending = [], []
    seen = set()
    for entry in overrides:
        key = (entry["context"], entry["source"], entry.get("comment", ""))
        translation = entry["translation"]
        placeholders = r"%(?:L?\d+|L?n|v|m|p)"
        if key in seen or sorted(re.findall(placeholders, key[1])) != sorted(re.findall(placeholders, translation)):
            raise ValueError(f"Duplicate override or mismatched placeholders: {key}")
        seen.add(key)
        live = active.get(key)
        if live is None:
            pending.append({"context": key[0], "source": key[1]})
            continue
        message = current.get(key)
        if message is None:
            context = contexts.get(key[0])
            if context is None:
                context = ET.SubElement(base, "context")
                ET.SubElement(context, "name").text = key[0]
                contexts[key[0]] = context
            message = ET.SubElement(context, "message")
            ET.SubElement(message, "source").text = key[1]
            if key[2]:
                ET.SubElement(message, "comment").text = key[2]
        old = message.find("translation")
        if old is not None:
            message.remove(old)
        translated = ET.SubElement(message, "translation")
        if live.get("numerus") == "yes":
            message.set("numerus", "yes")
            ET.SubElement(translated, "numerusform").text = translation
        else:
            translated.text = translation
        applied.append(key)
    return applied, pending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--release-json", type=Path)
    parser.add_argument("--release-commit")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--legacy-output", type=Path, help="Build a catalog from the Fritchii v5.4 source")
    parser.add_argument("--audit-output", type=Path, help="Write the exact source-key coverage and missing entries")
    parser.add_argument("--lupdate", default="lupdate")
    parser.add_argument("--lrelease", default="lrelease")
    args = parser.parse_args()
    if args.revision:
        print(revision())
        return
    if not args.source or (not args.legacy_output and not all((args.release_json, args.release_commit, args.output))):
        parser.error("--source, --release-json, --release-commit, --output are required")
    if not args.legacy_output and not re.fullmatch(r"[0-9a-f]{40}", args.release_commit):
        parser.error("Invalid release commit")
    source = args.source.resolve()
    base = ET.parse(source / "dist/languages/zh_CN.ts").getroot()
    if base.get("language") != "zh_CN":
        raise ValueError("Unexpected upstream TS language")
    overrides = json.loads((HERE / "overrides.zh_CN.json").read_text(encoding="utf-8"))
    source_translations = json.loads((HERE / "source.zh_CN.json").read_text(encoding="utf-8"))
    graphics = json.loads((HERE / "source.graphics.zh_CN.json").read_text(encoding="utf-8"))
    overrides += [entry for entry in graphics if args.legacy_output or not entry.get("legacy_only")]
    if args.legacy_output:
        legacy = json.loads((HERE / "overrides.fritchii.zh_CN.json").read_text(encoding="utf-8"))
        legacy_source = json.loads((HERE / "source.fritchii.zh_CN.json").read_text(encoding="utf-8"))
        legacy_keys = {(entry["context"], entry["source"], entry.get("comment", ""))
                       for entry in legacy + legacy_source}
        source_translations = [entry for entry in source_translations
                               if (entry["context"], entry["source"], entry.get("comment", "")) not in legacy_keys]
        overrides = overrides + legacy + legacy_source
    overrides += source_translations
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        template = tmp / "live.ts"
        subprocess.run([args.lupdate, "-I", "src", "src/yuzu", "src/qt_common", "-no-obsolete",
                        "-locations", "none", "-ts", str(template)], cwd=source, check=True)
        live = ET.parse(template).getroot()
        add_graphics_source_keys(live, source)
        add_runtime_source_keys(live, source)
        references = ()
        if args.legacy_output:
            upstream = ET.parse(HERE.parent / "dist/languages/zh_CN.ts").getroot()
            references = (messages(upstream),)
        reuse_translations(base, live, *references)
        applied, pending = align(base, live, overrides)
        add_runtime_contexts(base, live, source)
        coverage = source_coverage(base, live)
        if args.audit_output:
            args.audit_output.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
        print(f"Catalog text: {coverage['entries_with_catalog_text']}/{coverage['source_entries']} source entries.")
        add_qt_widget_labels(base)
        merged = tmp / "zh_CN.ts"
        ET.ElementTree(base).write(merged, encoding="utf-8", xml_declaration=True)
        qm = tmp / "zh_CN.qm"
        subprocess.run([args.lrelease, "-nounfinished", str(merged), "-qm", str(qm)], check=True)
        if args.legacy_output:
            args.legacy_output.write_bytes(qm.read_bytes())
            print(f"Aligned {len(applied)} overrides for Fritchii v5.4.")
            return
        asset = official_asset(json.loads(args.release_json.read_text(encoding="utf-8-sig")))
        digest = sha256_file(qm)
        name = f"{asset.tag}-{digest[:12]}.qm"
        catalogs = args.output / "catalogs"
        catalogs.mkdir(parents=True, exist_ok=True)
        (catalogs / name).write_bytes(qm.read_bytes())

    manifest = {
        "schema": 1,
        "upstream": UPSTREAM,
        "release_commit": args.release_commit,
        "revision": revision(),
        "asset": asdict(asset),
        "catalog": {"name": name, "sha256": digest},
        "translation": {
            "overrides_applied": len(applied),
            "needs_review": pending,
            "source_entries": coverage["source_entries"],
            "entries_with_catalog_text": coverage["entries_with_catalog_text"],
            "entries_with_chinese_text": coverage["entries_with_chinese_text"],
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Aligned {len(applied)} overrides; {len(pending)} need review.")


if __name__ == "__main__":
    main()
