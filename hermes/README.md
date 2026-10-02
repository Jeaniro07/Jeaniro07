# Hermes Agent × Descript × HelloMinds

Paket ini memasang dua layanan eksternal ke [Hermes Agent](https://hermes-agent.nousresearch.com)
dan menggabungkannya menjadi satu agent studio konten.

| Komponen | Apa yang dipasang | Cara terhubung |
|---|---|---|
| **Descript** ([web](https://web.descript.com/), [help](https://help.descript.com/)) | Server MCP `descript` + skill `descript-video-editor` | MCP resmi `https://api.descript.com/v2/mcp`, OAuth |
| **HelloMinds** ([builder](https://build.hellominds.ai/en/docs/get-started/account-setup)) | Skill `hellominds-mind` + CLI `hellominds.py` | Messaging API, header `X-Access-Key` |
| **Gabungan** | Skill `studio-orchestrator` | Hermes → Mind (naskah) → Underlord (edit) |

## Ringkasan layanan

**Descript** — editor video/audio berbasis transkrip. Agent AI-nya, **Underlord**, mengedit
dari perintah bahasa natural: hapus filler word, Studio Sound, caption, B-roll, klip.
Server MCP-nya menyediakan Discover, Import, Edit (Underlord), Publish, Export. Login lewat
OAuth lalu pilih Drive; tidak perlu API key.

**HelloMinds (Minds by Animoca Brands, ditenagai Ethoswarm)** — agent AI pribadi yang
persisten dan punya memori ("Mind"). Daftar dengan email, aktifkan Mind dalam ±1 menit,
atur **Connections** (API key provider) dan **Circles** (siapa yang boleh bicara dengan
Mind). Agent eksternal berbicara dengan Mind lewat Messaging API memakai Builder Access Key:
CreateConversation, ListConversations, GetConversation, SendMessage, GetMessageHistory,
SubscribeEvents (SSE).

## Pemasangan

```bash
cd hermes
./install.sh
```

Script ini:
1. menyalin 3 skill ke `~/.hermes/skills/`,
2. menambahkan server MCP `descript` ke `~/.hermes/config.yaml` (backup dibuat, server lain tidak diubah),
3. menambahkan `HELLOMINDS_ACCESS_KEY` dan `HELLOMINDS_API_BASE` kosong ke `~/.hermes/.env`.

Lalu:
1. Isi key dan base URL HelloMinds di `~/.hermes/.env` (lihat `.env.example`).
2. Jalankan `hermes`. Browser terbuka untuk login OAuth Descript.
   Di server tanpa layar, pakai skill opsional `mcp-oauth-remote-gateway`.
3. Cek dengan `hermes mcp list` dan `hermes skills list`.

## Contoh pemakaian

```
Pakai studio-orchestrator: minta Mind saya menulis naskah 60 detik tentang
peluncuran produk, lalu edit rekaman "launch-raw" di Descript sesuai naskah,
tambah caption dan Studio Sound, buat versi 9:16.
```

## Catatan

- Situs Descript dan HelloMinds tidak bisa diakses langsung dari lingkungan tempat paket
  ini dibuat. Detailnya diambil dari hasil pencarian dokumentasi resmi.
- **Base URL dan path endpoint Messaging API HelloMinds belum terverifikasi.** Ambil dari
  dokumentasi Builder; jika path berbeda, override lewat `HELLOMINDS_PATH_<OP>`.
- Jangan tambahkan server MCP Descript kedua secara manual. Koneksi ganda membuat sesi bentrok.
