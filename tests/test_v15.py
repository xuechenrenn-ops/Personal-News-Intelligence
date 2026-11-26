import unittest

from experiments.v15.sources import NEWS, SearchFetch, academic, excerpt, on_site


class V15Tests(unittest.TestCase):
    def test_search_answer_is_a_lead_and_budget_counts_failed_attempt(self):
        def transport(url, headers, payload):
            if url.endswith("/fetch"):
                raise RuntimeError("HTTP_403")
            return {"results": [{"url": "https://pubmed.ncbi.nlm.nih.gov/43058301/"}]}
        tools = SearchFetch({}, 2, transport)
        self.assertEqual(academic(tools, "2026-11-26", lambda lead: None)[0], [])
        self.assertEqual(tools.fetch("https://www.bbc.co.uk/news/article", NEWS), "")
        self.assertEqual(tools.search("next"), [])
        self.assertEqual([c["status"] for c in tools.calls], ["billable", "HTTP_403"])

    def test_fetch_excludes_other_domains_and_sections_without_body(self):
        self.assertFalse(on_site("https://bbc.co.uk.example.org/news", NEWS))
        self.assertFalse(on_site("https://login@bbc.co.uk/news", NEWS))
        self.assertEqual(excerpt("# " + "heading " * 20, "heading"), "")
