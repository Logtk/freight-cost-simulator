"""チャート配色の一元管理。

配色は`dataviz`スキルの検証済みパレットから採り、validatorで実際に検証した値のみを置く。

## 検証の経緯(重要な設計判断)

当初は赤字の4パターンにそれぞれ色を割り当てようとしたが、validatorで
`--pairs all`(表のチップのように任意の2つが並び得る文脈)を通したところ、
4色の組み合わせはいずれも不合格だった。

    blue+orange+aqua+violet  → dark で violet↔blue が ΔE 1.9 (protan) で FAIL
    blue+orange+aqua+red     → light で red↔orange が ΔE 7.1 (normal) で FAIL
    blue+orange+aqua+magenta → dark で magenta↔aqua が ΔE 1.6 (deutan) で FAIL

パレット側の仕様通り、all-pairs文脈でのカテゴリ識別は3枠が上限である。

そこで**エンコーディング自体を見直した**。4パターンは「運賃が安い」「積載率が低い」等の
自己説明的なラベルを持つ名義カテゴリであり、色相で識別させる必要がない。
このダッシュボードで色が本当に担うべき仕事は **Lv1視点 と Lv3視点 の対比**(2系列)である。

したがって:
  - カテゴリ色は2枠のみ(Lv1 / Lv3)。両モードで全チェックPASS(最悪ΔE 24.7/26.8)
  - 4パターンの識別はラベルが担う(色相を使わない)
  - 粗利影響額のような量は sequential(単一色相)で表す
  - アラート水準は status パレット。色だけに意味を持たせずアイコン+ラベルを併記する
"""

# --- カテゴリ(2枠のみ。用途: Lv1視点 vs Lv3視点の対比) -----------------
# validator: light(surface #ffffff) / dark(surface #0e1117) 共に all-pairs 全項目PASS
CATEGORICAL_LIGHT = {
    "lv3": "#2a78d6",  # 見えている世界(原価ドライバーあり)
    "lv1": "#eb6834",  # 見えていない世界(売上−輸送費のみ)
}
CATEGORICAL_DARK = {
    "lv3": "#3987e5",
    "lv1": "#d95926",
}

# --- sequential(単一色相・量を表す。粗利影響額など) ---------------------
# 明るい方が「ゼロに近い」。ordinal用途では light は 250 より明るくしない。
SEQUENTIAL_BLUE = {
    250: "#86b6ef",
    350: "#5598e7",
    450: "#2a78d6",
    550: "#1c5cab",
    650: "#104281",
}

# --- status(固定。カテゴリ色と混用しない) --------------------------------
# 光の当たり方に関わらず色だけで意味を運ばせない。必ずアイコン+ラベルを添える。
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

# --- チャートのクローム(軸・グリッド・インク) ---------------------------
CHROME_LIGHT = {
    "surface": "#ffffff",
    "text_primary": "#0b0b0b",
    "text_secondary": "#52514e",
    "text_muted": "#898781",
    "grid": "#e1e0d9",
    "baseline": "#c3c2b7",
}
CHROME_DARK = {
    "surface": "#0e1117",
    "text_primary": "#ffffff",
    "text_secondary": "#c3c2b7",
    "text_muted": "#898781",
    "grid": "#2c2c2a",
    "baseline": "#383835",
}


def categorical(dark: bool = False) -> dict:
    return CATEGORICAL_DARK if dark else CATEGORICAL_LIGHT


def chrome(dark: bool = False) -> dict:
    return CHROME_DARK if dark else CHROME_LIGHT
