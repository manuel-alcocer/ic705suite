# Spanish translations

Every `.po` file in this folder is loaded; they are split by area only to keep
them manageable. The source language is English (the `msgid`), the Spanish
text is the `msgstr`. Placeholders such as `{band}` must appear in both.
`tests/test_i18n.py` fails when a string marked with `_()` or `N_()` in the
code has no translation here.
