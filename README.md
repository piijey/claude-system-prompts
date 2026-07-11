# claude-system-prompts

- en: [System Prompts - Claude Platform Docs](https://platform.claude.com/docs/en/release-notes/system-prompts)
- ja: [システムプロンプト - Claude Platform Docs](https://platform.claude.com/docs/ja/release-notes/system-prompts)


ページをダウンロード

```
curl https://platform.claude.com/docs/en/release-notes/system-prompts.md > data/system-prompts_en.md
curl https://platform.claude.com/docs/ja/release-notes/system-prompts.md > data/system-prompts_ja.md
```

## テキスト抽出・分析

表現・スタイル関連指示を抽出

```
python scripts/find_style_rules.py data/system-prompts_en.md -o style_rules_report.md
```

"the human" → "the person" / "the user" の呼称推移を集計・グラフ化: 
[pronoun_shift.ipynb](pronoun_shift.ipynb)
