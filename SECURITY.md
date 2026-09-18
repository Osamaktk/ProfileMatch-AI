# Security

Do not report credentials, private documents, or personal contact records in public issues. Use GitHub private vulnerability reporting if it is enabled; otherwise contact the repository owner privately before sharing sensitive details.

Keep populated environment files and local datasets outside version control. Environment examples must contain placeholders only. Rotate any credential exposed in a commit; ignoring the file later does not remove it from history.

Run with synthetic data during development. Treat search results, uploaded files, model outputs, and third-party websites as untrusted input. Review authentication, upload limits, CORS, outbound network access, and provider costs before exposing an application publicly.

No support SLA or production security certification is implied.
