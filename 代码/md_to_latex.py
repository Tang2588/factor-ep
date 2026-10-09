# -*- coding: utf-8 -*-
"""把中文 Markdown 技术文档转换为可编译的 LaTeX。

    python md_to_latex.py 输入.md 输出.tex -t "文档标题" -d "2026 年 9 月 24 日"

支持本项目文档用到的语法：标题、段落、有序/无序列表、表格、引用块、
代码块、图片、行内代码、加粗与数学公式。表格按内容自动判断左右对齐，
并整体缩放以适配页宽。

公式写法：

- ``$...$`` 为行内公式；
- ````math` 代码块为独立公式，块内直接写 LaTeX（可写完整的 equation、
  align 等环境）；
- ````latex` 代码块为原始 LaTeX 透传，用于编号等排版控制。

设计取舍：

- 代码块不用 verbatim，改用 ``ttfamily`` 环境，这样中文与希腊字母等
  Unicode 字符能借 ctex 的字体正常显示；
- 特殊字符统一转义，避免 ``_``、``%``、``&`` 等在 LaTeX 里报错。
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt


ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

# 这些 Unicode 符号在正文里比在数学模式里更常见，直接交给字体渲染
# 可能缺字，因此映射为等价的 LaTeX 写法。
UNICODE_MAP = {
    "α": r"$\alpha$",
    "β": r"$\beta$",
    "γ": r"$\gamma$",
    "δ": r"$\delta$",
    "ε": r"$\varepsilon$",
    "ζ": r"$\zeta$",
    "η": r"$\eta$",
    "θ": r"$\theta$",
    "κ": r"$\kappa$",
    "λ": r"$\lambda$",
    "μ": r"$\mu$",
    "ν": r"$\nu$",
    "ξ": r"$\xi$",
    "π": r"$\pi$",
    "ρ": r"$\rho$",
    "σ": r"$\sigma$",
    "τ": r"$\tau$",
    "φ": r"$\varphi$",
    "χ": r"$\chi$",
    "ψ": r"$\psi$",
    "ω": r"$\omega$",
    "Γ": r"$\Gamma$",
    "Δ": r"$\Delta$",
    "Θ": r"$\Theta$",
    "Λ": r"$\Lambda$",
    "Π": r"$\Pi$",
    "Σ": r"$\Sigma$",
    "Φ": r"$\Phi$",
    "Ψ": r"$\Psi$",
    "Ω": r"$\Omega$",
    "≠": r"$\neq$",
    "≥": r"$\ge$",
    "≤": r"$\le$",
    "×": r"$\times$",
    "±": r"$\pm$",
    "→": r"$\rightarrow$",
    "−": "-",
    "\u00a0": " ",
}


def escape_latex(text: str) -> str:
    """单遍转义：Unicode 符号直接映射成 LaTeX 写法，其余特殊字符做转义。

    必须一遍完成 —— 分两步（先替换符号再转义）会把刚插入的 ``$`` 和 ``\\``
    再转义一次，变成 ``\\$\\textbackslash{}neq\\$``。
    """
    parts = []
    for char in text:
        if char in UNICODE_MAP:
            parts.append(UNICODE_MAP[char])
        elif char in ESCAPE_MAP:
            parts.append(ESCAPE_MAP[char])
        else:
            parts.append(char)
    return "".join(parts)


INLINE_MATH_RE = re.compile(r"\$(.+?)\$", re.S)


def render_text(content: str) -> str:
    """文本片段 → LaTeX：``$...$`` 原样保留为行内公式，其余字符转义。"""
    parts: list[str] = []
    position = 0
    for match in INLINE_MATH_RE.finditer(content):
        parts.append(escape_latex(content[position : match.start()]))
        parts.append("$" + match.group(1) + "$")
        position = match.end()
    parts.append(escape_latex(content[position:]))
    return "".join(parts)


def render_inline(token) -> str:
    """把 inline token 的子节点渲染成 LaTeX 片段。"""
    parts: list[str] = []
    for child in token.children or []:
        kind = child.type
        if kind == "text":
            parts.append(render_text(child.content))
        elif kind == "code_inline":
            parts.append(r"\texttt{" + escape_latex(child.content) + "}")
        elif kind == "strong_open":
            parts.append(r"\textbf{")
        elif kind == "strong_close":
            parts.append("}")
        elif kind == "em_open":
            parts.append(r"\emph{")
        elif kind == "em_close":
            parts.append("}")
        elif kind == "softbreak":
            parts.append("\n")
        elif kind == "hardbreak":
            parts.append(r"\\")
        elif kind == "image":
            # 独立成段的图片在块级处理，这里只保留 alt 文本
            parts.append(escape_latex(child.content or ""))
        else:
            parts.append(render_text(child.content or ""))
    return "".join(parts)


def _paragraph_image(token) -> tuple[str, str] | None:
    """段落若只包含一张图片，返回 (路径, alt)，否则返回 None。"""
    children = token.children or []
    images = [c for c in children if c.type == "image"]
    if len(images) != 1:
        return None
    texts = [c for c in children if c.type == "text" and c.content.strip()]
    if texts:
        return None
    image = images[0]
    # markdown_it 会把中文路径做百分号编码，必须解码，否则 LaTeX 里的 % 会被当成注释
    source = unquote(image.attrs.get("src") or "").lstrip("./")
    return source, (image.content or "").strip()


def _column_spec(rows: list[list[str]], ncols: int) -> str:
    """按列内容判断对齐：整列都是数字与百分号则右对齐，否则左对齐换行。"""
    specs = []
    for column in range(ncols):
        cells = [row[column] for row in rows if column < len(row) and row[column].strip()]
        numeric = bool(cells) and all(
            re.fullmatch(r"[-+0-9.,%:/\s]*", cell) and any(ch.isdigit() for ch in cell)
            for cell in cells
        )
        specs.append("R" if numeric else "Y")
    return "".join(specs)


def _cell(text: str) -> str:
    """表格单元格：换行标记转成 LaTeX 换行。"""
    return text.replace("<br>", r"\newline ").strip()


def convert_body(markdown: str) -> str:
    """Markdown 正文 → LaTeX 正文（不含导言区）。"""
    parser = MarkdownIt("commonmark", {"html": True}).enable("table")
    tokens = parser.parse(markdown)

    lines: list[str] = []
    list_stack: list[str] = []
    quote_depth = 0
    table_rows: list[list[str]] = []
    in_table = False
    in_table_head = False

    for index, token in enumerate(tokens):
        kind = token.type

        if kind == "heading_open":
            level = int(token.tag[1])
            text = render_inline(tokens[index + 1])
            # 标题自带中文编号（「一、」「1.1」），因此用带星号的命令避免
            # LaTeX 再编一层号，同时手工登记进目录。
            command = {2: "section", 3: "subsection", 4: "subsubsection"}.get(
                level, "section"
            )
            lines.append(r"\phantomsection")
            lines.append(f"\\addcontentsline{{toc}}{{{command}}}{{{text}}}")
            lines.append(f"\\{command}*{{{text}}}")
            lines.append("")
        elif kind == "heading_close":
            continue

        elif kind == "paragraph_open":
            if quote_depth:
                continue
            image = None
            if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
                image = _paragraph_image(tokens[index + 1])
            if image:
                source, caption = image
                lines.append(r"\begin{figure}[htbp]")
                lines.append(r"\centering")
                lines.append(r"\includegraphics[width=0.96\textwidth]{" + source + "}")
                if caption:
                    lines.append(r"\caption*{" + escape_latex(caption) + "}")
                lines.append(r"\end{figure}")
                lines.append("")
            elif list_stack:
                text = render_inline(tokens[index + 1])
                lines.append(text)
            else:
                text = render_inline(tokens[index + 1])
                lines.append(text)
                lines.append("")
        elif kind == "paragraph_close":
            continue

        elif kind == "bullet_list_open":
            lines.append(r"\begin{itemize}[leftmargin=2em,itemsep=2pt,topsep=3pt]")
            list_stack.append("itemize")
        elif kind == "ordered_list_open":
            lines.append(r"\begin{enumerate}[leftmargin=2.2em,itemsep=2pt,topsep=3pt]")
            list_stack.append("enumerate")
        elif kind in ("bullet_list_close", "ordered_list_close"):
            name = list_stack.pop() if list_stack else "itemize"
            lines.append(f"\\end{{{name}}}")
            lines.append("")
        elif kind == "list_item_open":
            lines.append(r"\item ")
        elif kind == "list_item_close":
            lines.append("")

        elif kind == "blockquote_open":
            quote_depth += 1
            lines.append(r"\begin{quote}")
        elif kind == "blockquote_close":
            quote_depth = max(0, quote_depth - 1)
            lines.append(r"\end{quote}")
            lines.append("")

        elif kind == "fence":
            info = (token.info or "").strip().lower()
            body = token.content.rstrip("\n")
            if info == "math":
                # 公式块：内容本身就是 LaTeX，原样嵌入，不做任何转义。
                # 若块内已写完整环境（如 equation/align）则直接使用，
                # 否则按无编号的独立公式处理。
                if re.search(r"\\begin\{", body):
                    lines.append(body)
                else:
                    lines.append(r"\[")
                    lines.append(body)
                    lines.append(r"\]")
                lines.append("")
            elif info == "latex":
                # 原始 LaTeX 片段：原样透传，用于编号等排版控制。
                lines.append(body)
                lines.append("")
            else:
                lines.append(r"\begin{quote}\ttfamily\small")
                for code_line in body.split("\n"):
                    lines.append(escape_latex(code_line) + r"\\")
                lines.append(r"\end{quote}")
                lines.append("")

        elif kind == "table_open":
            in_table = True
            table_rows = []
        elif kind == "thead_open":
            in_table_head = True
        elif kind == "thead_close":
            in_table_head = False
        elif kind == "tr_open":
            table_rows.append([])
        elif kind in ("th_open", "td_open"):
            if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
                table_rows[-1].append(_cell(render_inline(tokens[index + 1])))
        elif kind == "table_close":
            in_table = False
            ncols = max(len(row) for row in table_rows)
            for row in table_rows:
                while len(row) < ncols:
                    row.append("")
            header, body_rows = table_rows[0], table_rows[1:]
            spec = _column_spec(body_rows or table_rows, ncols)
            if ncols >= 11:
                size = r"\scriptsize"
            elif ncols >= 8:
                size = r"\footnotesize"
            elif ncols >= 6:
                size = r"\small"
            else:
                size = ""
            lines.append(r"\begin{table}[htbp]")
            lines.append(r"\centering")
            if size:
                lines.append(size)
            lines.append(r"\begin{tabularx}{\textwidth}{@{}" + spec + r"@{}}")
            lines.append(r"\toprule")
            lines.append(" & ".join(header) + r" \\")
            lines.append(r"\midrule")
            for row in body_rows:
                lines.append(" & ".join(row) + r" \\")
            lines.append(r"\bottomrule")
            lines.append(r"\end{tabularx}")
            lines.append(r"\end{table}")
            lines.append("")

    return "\n".join(lines)


def split_title(markdown: str) -> tuple[str | None, str]:
    """取出文首的一级标题作为文档标题，正文里不再重复。"""
    match = re.match(r"\s*#\s+(.+?)\s*\n", markdown)
    if not match:
        return None, markdown
    return match.group(1).strip(), markdown[match.end() :]


PREAMBLE = r"""% !TeX program = xelatex
% !TeX encoding = UTF-8
\documentclass[UTF8,a4paper,11pt]{ctexart}

\usepackage[a4paper,margin=2.3cm]{geometry}
\usepackage{amsmath,amssymb,bm,mathtools}
\usepackage{booktabs,tabularx,array,longtable}
\usepackage{graphicx}
\usepackage{float}
\usepackage{xcolor}
\usepackage{enumitem}
\usepackage{caption}
\usepackage{hyperref}
\usepackage{url}

\newcolumntype{Y}{>{\raggedright\arraybackslash}X}
\newcolumntype{R}{>{\raggedleft\arraybackslash}X}
\setlength{\parindent}{2em}
\setlength{\parskip}{0.2em}
\setlength{\tabcolsep}{5pt}
\renewcommand{\arraystretch}{1.2}
\urlstyle{same}
"""


def build_document(body: str, title: str, date: str) -> str:
    """拼装完整的 .tex 文件。"""
    return (
        PREAMBLE
        + "\\hypersetup{\n"
        + "  colorlinks=true,\n"
        + "  linkcolor=blue!55!black,\n"
        + "  urlcolor=blue!55!black,\n"
        + f"  pdftitle={{{title}}}\n"
        + "}\n\n"
        + f"\\title{{\\textbf{{{title}}}}}\n"
        + "\\author{}\n"
        + f"\\date{{{date}}}\n\n"
        + "\\begin{document}\n"
        + "\\maketitle\n"
        + "\\tableofcontents\n"
        + "\\newpage\n\n"
        + body
        + "\n\\end{document}\n"
    )


def convert(source: Path, output: Path, title: str | None, date: str) -> Path:
    markdown = source.read_text(encoding="utf-8")
    found_title, body_markdown = split_title(markdown)
    document_title = title or found_title or source.stem
    body = convert_body(body_markdown)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_document(body, document_title, date), encoding="utf-8")
    return output.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把中文 Markdown 技术文档转换成可编译的 LaTeX。"
    )
    parser.add_argument("source", type=Path, help="输入 Markdown")
    parser.add_argument("output", type=Path, help="输出 .tex")
    parser.add_argument("-t", "--title", default=None, help="文档标题，默认取文首一级标题")
    parser.add_argument("-d", "--date", default="", help="标题页日期")
    args = parser.parse_args()
    print(convert(args.source, args.output, args.title, args.date))


if __name__ == "__main__":
    main()
