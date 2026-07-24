import unittest

from open_webui.utils.file_search import (
    FileSearchCandidate,
    FileSearchMatch,
    FileSearchRuntimeConfig,
    _fallback_selection,
    build_sources,
    clear_file_search_cache,
    decrypt_password,
    encrypt_password,
    strong_filename_match,
    terms,
    validate_relative_directory,
    list_cached_directories,
    rank_candidates,
)


class FileSearchTests(unittest.TestCase):
    def test_filename_separators_are_word_boundaries(self):
        self.assertTrue(
            {"gpu", "architecture", "overview"}
            <= terms("gpu_architecture-overview.pdf")
        )

    def test_strong_filename_match_ignores_retrieval_words(self):
        path = r"\\server\share\gpu_architecture_overview.pdf"
        self.assertTrue(
            strong_filename_match("find current gpu architecture", path)
        )
        self.assertTrue(strong_filename_match("目前的 GPU 架构", path))
        self.assertFalse(strong_filename_match("gpu deployment", path))

    def test_directory_must_stay_relative_to_configured_share(self):
        self.assertEqual(
            validate_relative_directory(r"AI\SOP"),
            r"AI\SOP",
        )
        for unsafe in ("..", r"AI\..\secret", r"C:\Windows", r"\\server\share"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    validate_relative_directory(unsafe)

    def test_password_is_encrypted_at_rest(self):
        encrypted = encrypt_password("p@ssw0rd")
        self.assertNotIn("p@ssw0rd", encrypted)
        self.assertEqual(decrypt_password(encrypted), "p@ssw0rd")

    def test_precise_fallback_returns_one_strong_filename_match(self):
        candidates = [
            FileSearchCandidate(
                path=r"\\server\share\gpu_architecture_overview.pdf",
                relative_path="gpu_architecture_overview.pdf",
                preview="GPU resource architecture",
                score=9.5,
            ),
            FileSearchCandidate(
                path=r"\\server\share\gpu_setup.pdf",
                relative_path="gpu_setup.pdf",
                preview="GPU setup",
                score=5.5,
            ),
        ]
        selected = _fallback_selection("gpu architecture", candidates, 3)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][0].relative_path, "gpu_architecture_overview.pdf")

        selected = _fallback_selection("目前的 GPU 架构", candidates, 3)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][0].relative_path, "gpu_architecture_overview.pdf")

    def test_sop_fallback_can_return_multiple_complementary_files(self):
        candidates = [
            FileSearchCandidate(
                path=rf"\\server\share\it_{index}.pdf",
                relative_path=f"it_{index}.pdf",
                preview="IT process",
                score=5 - index,
            )
            for index in range(4)
        ]
        selected = _fallback_selection("整理 IT SOP 流程", candidates, 3)
        self.assertEqual(len(selected), 3)

    def test_multi_file_context_budget_is_shared_fairly(self):
        matches = [
            FileSearchMatch(
                path=rf"\\server\share\it_{index}.pdf",
                relative_path=f"it_{index}.pdf",
                content=str(index) * 20000,
                confidence=0.8,
                reason="Relevant to the requested SOP",
            )
            for index in range(3)
        ]
        sources = build_sources(matches, 30000)
        self.assertEqual(len(sources), 3)
        self.assertEqual([len(source["document"][0]) for source in sources], [10000] * 3)

    def test_directory_listing_cache_is_scoped_to_user_and_path(self):
        class Store:
            config = type("Config", (), {"cache_ttl_seconds": 300})()
            calls = 0

            def list_directories(self, directory):
                self.calls += 1
                return [{"name": directory or "root", "path": directory}]

        store = Store()
        clear_file_search_cache("directory-cache-user")
        first = list_cached_directories(
            store, "directory-cache-user", 1, r"AI\SOP"
        )
        second = list_cached_directories(
            store, "directory-cache-user", 1, r"AI\SOP"
        )
        self.assertEqual(first, second)
        self.assertEqual(store.calls, 1)
        clear_file_search_cache("directory-cache-user")

    def test_index_progress_reports_file_batches_and_summary(self):
        class Store:
            config = FileSearchRuntimeConfig(server="server", share="share")

            def iter_files(self, directory):
                for index in range(4):
                    name = f"gpu_{index}.txt"
                    yield rf"\\server\share\{name}", name
                yield r"\\server\share\image.png", "image.png"

            def read_text(self, path, limit):
                return "GPU architecture"

        progress = []
        clear_file_search_cache("progress-user")
        candidates = rank_candidates(
            Store(),
            "progress-user",
            1,
            "",
            "gpu architecture",
            progress.append,
        )
        self.assertEqual(len(candidates), 4)
        inspect_events = [
            event for event in progress if event["stage"] == "inspect"
        ]
        self.assertEqual(
            [len(event["files"]) for event in inspect_events],
            [3, 1],
        )
        self.assertEqual(
            progress[-1],
            {
                "stage": "complete",
                "discovered": 5,
                "supported": 4,
                "indexed": 4,
                "unreadable": 0,
            },
        )
        clear_file_search_cache("progress-user")


if __name__ == "__main__":
    unittest.main()
