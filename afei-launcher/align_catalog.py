# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an external Qt catalog for the exact upstream release commit."""

import argparse
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


def add_qt_button_labels(catalog):
    context = ET.SubElement(catalog, "context")
    ET.SubElement(context, "name").text = "QPlatformTheme"
    for source, translated in (
        ("OK", "确定"), ("Cancel", "取消"), ("Close", "关闭"), ("Apply", "应用"),
        ("&Yes", "是(&Y)"), ("&No", "否(&N)"), ("Help", "帮助"), ("Reset", "重置"),
        ("Open", "打开"), ("Save", "保存"), ("Discard", "丢弃"), ("Retry", "重试"),
        ("Restore Defaults", "恢复默认值"),
    ):
        message = ET.SubElement(context, "message")
        ET.SubElement(message, "source").text = source
        ET.SubElement(message, "translation").text = translated


def revision():
    return hashlib.sha256(b"".join((HERE / name).read_bytes().replace(b"\r\n", b"\n") for name in (
        "align_catalog.py", "overrides.zh_CN.json"
    ))).hexdigest()


def message_key(context, message):
    return (context.findtext("name"), message.findtext("source"), message.findtext("comment") or "")


def messages(root):
    return {message_key(context, message): message
            for context in root.findall("context") for message in context.findall("message")}


def align(base, template, overrides):
    active = messages(template)
    current = messages(base)
    contexts = {context.findtext("name"): context for context in base.findall("context")}
    applied, pending = [], []
    seen = set()
    for entry in overrides:
        key = (entry["context"], entry["source"], entry.get("comment", ""))
        translation = entry["translation"]
        placeholders = r"%(?:L?\d+|L?n)"
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
    if args.legacy_output:
        legacy = json.loads((HERE / "overrides.fritchii.zh_CN.json").read_text(encoding="utf-8"))
        overrides = overrides + legacy
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        template = tmp / "live.ts"
        subprocess.run([args.lupdate, "src/yuzu", "src/qt_common", "-no-obsolete",
                        "-locations", "none", "-ts", str(template)], cwd=source, check=True)
        live = ET.parse(template).getroot()
        if args.legacy_output:
            # lupdate can omit the namespace when resolving these QObject classes.
            for context in live.findall("context"):
                name = context.find("name")
                if name.text in {"Home", "PrecompileGate"}:
                    name.text = f"FritchiiUi::{name.text}"
        applied, pending = align(base, live, overrides)
        add_qt_button_labels(base)
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
        "translation": {"overrides_applied": len(applied), "needs_review": pending},
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Aligned {len(applied)} overrides; {len(pending)} need review.")


if __name__ == "__main__":
    main()
