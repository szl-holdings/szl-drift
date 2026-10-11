# szl-drift

Origin-drift receipts. **Not a second flagship.**

<!-- szl:header v1 -->
<!-- Native identity: szl-brand@4309f6fce89179331b0b3ed8c99494e76c5be256; Orbit v2 icon. -->
<p>
  <a href="https://github.com/szl-holdings">
    <img src="https://raw.githubusercontent.com/szl-holdings/szl-brand/4309f6fce89179331b0b3ed8c99494e76c5be256/kit/logos/orbit-v2/suite/szl-icon-32.svg" alt="SZL Holdings — Orbit v2" width="32" height="32" />
  </a>
</p>

**Control before action. Evidence after.**

[Product](https://a-11-oy.com) · [Proof](https://a11oy.net) · [GitHub organization](https://github.com/szl-holdings)
<!-- /szl:header -->

Compares:

| Origin | Probe | Meaning |
|---|---|---|
| Product | `https://a-11-oy.com/api/a11oy/v1/honest` + `/readyz` | Runtime doctrine + readiness |
| Proof | `https://a11oy.net/health.json` | Static GitHub Pages document. Not DSSE-LIVE. |
| Preview | Ring 1 kernel catalog | This surface is not the product origin |

A `200` from product `/readyz` is **not** a production certificate. Factory bind `AO-2026-08-29-001` still holds that certificate **CLOSED**.

## Run

```bash
python3 probe.py
```

Exit `0` always prints JSON. Missing, malformed, or non-object origin responses populate `errors`;
a known HTTP status is retained. No upstream response can set `certified_production_ready` to true.

## Honesty

- Doctrine v11 LOCKED
- Λ = Conjecture 1
- locked-8 paints only when live `locked_formula_count === 8`
- Warhacker v1.0.0 is archived
- Apache-2.0

Interactive verify stays on [a-11-oy.com/verify](https://a-11-oy.com/verify).
