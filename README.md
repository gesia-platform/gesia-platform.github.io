# GESIA website

Static GESIA company website, restored from the company's original private source repository for publication at <https://gesia.gesiaplatform.com/>.

The site includes a standalone recovery of the company's original published GitBook content:

- 90 English documentation pages under `/docs/en/`
- 74 Korean documentation pages under `/docs/kr/`
- 3 English and 3 Korean carbon-credit product pages under `/carbon-credits/`
- 202 locally preserved documentation images

All first-party documentation and product links stay on `gesia.gesiaplatform.com`; no page depends on Internet Archive navigation.

Run `python3 scripts/recover-gitbook.py` to regenerate the documentation from the still-published company GitBook sources, and `node scripts/check-static.mjs` to validate local references.
