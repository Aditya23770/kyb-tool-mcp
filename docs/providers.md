# Providers

A provider is a dumb adapter: it fetches data and maps it to a canonical schema. It never decides
anything. All rules live in `checks/` and `policy.py`.

| Check | Built-in providers | Licence | Notes |
| --- | --- | --- | --- |
| registry | `companies_house` | Open Government Licence | Free key; ~600 requests / 5 min (rate limited + cached) |
| registry | `gleif` | CC0 | No key; global coverage, but no officers or owners |
| sanctions | `ofac_sdn` | US government, public domain | Local index |
| sanctions | `un_consolidated` | UN, public | Local index |
| sanctions + pep | `opensanctions_bulk` | **CC BY-NC 4.0** | **Non-commercial use only.** Opt-in |
| website | `website_http` | n/a | Max 5 redirects, 2 MB cap, 15 s timeout |
| website | `rdap` | public | Domain registration date |

Enable providers in `providers.yaml` (`kyb-tool-mcp init` writes one) and point `KYB_PROVIDERS_FILE` at it:

```yaml
registry: [companies_house, gleif]
screening: [ofac_sdn, un_consolidated, opensanctions_bulk]
pep: [opensanctions_bulk]
website: [website_http, rdap]
```

Then rebuild the index with the extra list:
`kyb-tool-mcp update-lists --sources ofac_sdn,un_consolidated,opensanctions_bulk`.

## Writing a new provider

1. **Implement the protocol** from `providers/base.py`:

   ```python
   class MyRegistry:
       name = "my_registry"

       def supports(self, country: str) -> bool: ...
       async def lookup(self, country, reg_no, name) -> Evidence[CompanyRecord] | None: ...
   ```

   - Use `ProviderContext.get(...)` for HTTP. It gives you retries on 429/5xx, rate limiting and caching.
   - Return `None` for "not found". Raise `ProviderError` for failures; the pipeline turns it into a
     `PROVIDER_ERROR` flag (fail closed).
   - Wrap the result in `Evidence.build(provider=..., kind=..., request=..., payload=..., raw=raw_json)`
     so every flag can cite it.

2. **Register it.** Construct it in `ReviewPipeline._registry_providers` (or the screening/website equivalent).
3. **Add a sanctions list.** Write a loader in `providers/sanctions/loaders/` that yields `ListEntity`,
   then add it to `SOURCES` in `lists.py`. The index, blocking and adjudication work unchanged.
4. **Test it offline.** Extend `kyb_tool_mcp.testing.FakeWorld` or add a fixture file. Unit tests must
   not touch the network. Mark real-API smoke tests `@pytest.mark.live`.
5. **Document the licence** in the table above and in `NOTICE`.
