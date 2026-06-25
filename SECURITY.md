# Security Policy

AudioArca handles forensic audio and text evidence, so public issues and pull requests must not include secrets, credentials, case files, voice recordings, transcripts, report PDFs, or identifying evidence metadata.

## Reporting a Vulnerability

Use GitHub private vulnerability reporting if it is enabled for the repository. If it is not enabled, open a minimal public issue that describes the affected area without exploit details or sensitive data, then coordinate disclosure privately with the maintainers.

## Sensitive Data Handling

- Keep `.env` files local and commit only placeholder configuration in `example.env`.
- Keep uploaded evidence, generated reports, model weights, and local sample data out of git.
- Rotate any credential that was ever committed or pasted into a public issue, even if the commit or issue was later deleted.
- Use sanitized fixtures in tests. Real forensic material belongs in controlled storage, not in the repository.
