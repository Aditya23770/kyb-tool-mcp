# Security policy

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/Aditya23770/kyb-tool-mcp/security/advisories/new)
rather than opening a public issue. You will get a reply within 7 days.

In scope: anything that could leak API keys or screened data, execute code from downloaded lists or
websites, or cause a sanctioned party to be approved (a matching false negative caused by crafted input).

kyb-tool-mcp runs locally. It sends registry lookups to Companies House / GLEIF, domain lookups to
rdap.org, and fetches the business website you ask it to check. Nothing else leaves your machine.
