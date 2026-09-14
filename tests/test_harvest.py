"""Page identity fixtures are in-memory and never contact or rewrite the library."""
import copy
from io import BytesIO
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import harvest

TARGET = 'Liver resection: a randomized trial in 2025'
OTHER = 'Liver resection: a randomized trial in 2024'


def request_log(content_type='text/html', status=200):
    return {'status': status, 'content_type': content_type,
            'requested_url': 'https://example.org/original',
            'final_url': 'https://example.org/final', 'sha256': 'original-hash',
            'checked_at': '2026-09-14T00:00:00+00:00',
            'attempts': [{'status': status, 'url': 'https://example.org/final'}],
            'bytes': 1234}


def html(body='', head=''):
    return f'<html><head>{head}</head><body>{body}</body></html>'.encode()


def pdf(*pages):
    """Make actual extractable PDFs with explicit first-page text geometry."""
    from pypdf import PdfWriter
    from pypdf.generic import (DictionaryObject, NameObject, DecodedStreamObject)
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    for rows in pages:
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        commands = []
        for text, size, y in rows:
            text = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append(f'BT /F1 {size} Tf 1 0 0 1 40 {y} Tm ({text}) Tj ET')
        stream = DecodedStreamObject()
        stream.set_data('\n'.join(commands).encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class PageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(harvest, 'fetch', side_effect=AssertionError('No live network'))
        self.network.start()

    def tearDown(self):
        self.network.stop()

    def parse(self, content, content_type='text/html', title=TARGET, log=None):
        return harvest.parse_page_content(content, title, log or request_log(content_type))

    def test_primary_heading_matches_with_inline_markup_and_site_title(self):
        content = html('<main><h1>Liver resection: a <em>randomized</em> trial in 2025.</h1></main>',
                       '<title>Journal of Surgery</title>')
        result, _ = self.parse(content)
        self.assertTrue(result['title_present'])
        self.assertEqual(result['title_match_source'], 'h1')

    def test_reference_or_prose_text_cannot_confirm_an_article(self):
        for body in (f'<p>{TARGET}</p>', f'<section id="references"><h2>{TARGET}</h2></section>'):
            with self.subTest(body=body):
                result, text = self.parse(html('<h1>An entirely different study</h1>' + body))
                self.assertIn(TARGET, text)
                self.assertFalse(result['title_present'])

    def test_nearly_identical_title_and_related_h2_do_not_match(self):
        result, _ = self.parse(html(f'<main><h1>{OTHER}</h1><h2>{TARGET}</h2></main>'))
        self.assertGreater(result['title_similarity'], .9)
        self.assertFalse(result['title_present'])
        self.assertEqual(result['title_candidates'], [{'source': 'h1', 'title': OTHER}])

    def test_matching_related_h1_is_excluded(self):
        result, _ = self.parse(html(f'<main><h1>{OTHER}</h1>'
                                   f'<aside><h1>{TARGET}</h1></aside></main>'))
        self.assertFalse(result['title_present'])

    def test_page_title_precedes_unlabelled_h2_recommendation(self):
        result, _ = self.parse(html(f'<main><h2>{TARGET}</h2></main>', f'<title>{OTHER}</title>'))
        self.assertFalse(result['title_present'])

    def test_citation_and_main_heading_conflict_cannot_confirm(self):
        for primary, citation in ((OTHER, TARGET), (TARGET, OTHER)):
            with self.subTest(primary=primary):
                result, _ = self.parse(html(f'<h1>{primary}</h1>',
                                           f'<meta name="citation_title" content="{citation}">'))
                self.assertFalse(result['title_present'])
                self.assertTrue(result['title_conflict'])

    def test_head_metadata_and_explicit_browser_suffix_are_allowed(self):
        for head in (f'<meta name="citation_title" content="{TARGET}">',
                     f'<meta property="og:title" content="{TARGET}">',
                     f'<title>{TARGET} - PubMed</title>'):
            with self.subTest(head=head):
                self.assertTrue(self.parse(html(head=head))[0]['title_present'])
        self.assertFalse(self.parse(html(head=f'<title>A commentary on {TARGET}</title>'))[0]['title_present'])
        for suffix in ('five-year follow-up', 'five-year follow-up - PubMed'):
            with self.subTest(suffix=suffix):
                self.assertFalse(self.parse(html(head=f'<title>{TARGET} - {suffix}</title>'))[0]['title_present'])

    def test_citation_meta_inside_body_is_not_a_primary_record(self):
        result, _ = self.parse(html(f'<meta name="citation_title" content="{TARGET}">'))
        self.assertFalse(result['title_present'])

    def test_only_two_fixed_sages_suffixes_are_removed(self):
        for suffix in (' from the SAGES Video Library', ' - A SAGES Publication'):
            with self.subTest(suffix=suffix):
                self.assertTrue(self.parse(html(head=
                    f'<meta property="og:title" content="{TARGET}{suffix}">'))[0]['title_present'])
                for wrong in (OTHER + suffix, TARGET + ' follow-up' + suffix,
                              TARGET + suffix + ' additional material'):
                    self.assertFalse(self.parse(html(head=
                        f'<meta property="og:title" content="{wrong}">'))[0]['title_present'])
        self.assertFalse(self.parse(html(head=
            f'<meta property="og:title" content="{TARGET} from the SAGES archive">'))[0]['title_present'])

    def test_complete_citation_allows_single_long_h1_at_colon_boundary(self):
        main = ('Restrictive Strategy vs Usual Care for Cholecystectomy in Patients '
                'With Abdominal Pain and Gallstones')
        title = main + ': 5-Year Follow-Up of the SECURE Randomized Clinical Trial.'
        result, _ = self.parse(html(f'<main><h1>{main}</h1><p>Separate subtitle</p></main>',
            f'<meta name="citation_title" content="{title}">'), title=title)
        self.assertTrue(result['title_present'])
        self.assertFalse(result['title_conflict'])

    def test_split_h1_does_not_allow_truncation_or_original_followup_confusion(self):
        main = 'Long-term clinical outcomes after liver resection in the 2025 trial'
        title = main + ': five-year follow-up report'
        for heading, citation in ((main[:-6], title),
                                  (main.replace('2025', '2024'), title),
                                  (main, main + ': original trial report'),
                                  (main, '')):
            with self.subTest(heading=heading, citation=citation):
                result, _ = self.parse(html(f'<h1>{heading}</h1>',
                    f'<meta name="citation_title" content="{citation}">'), title=title)
                self.assertFalse(result['title_present'])
        for heading in ('Trial', 'Liver resection'):
            short = heading + ': a randomized controlled trial with long-term follow-up'
            self.assertFalse(self.parse(html(f'<h1>{heading}</h1>',
                f'<meta name="citation_title" content="{short}">'), title=short)[0]['title_present'])
        for separator in (' - ', ' '):
            no_colon = main + separator + 'five-year follow-up report'
            self.assertFalse(self.parse(html(f'<h1>{main}</h1>',
                f'<meta name="citation_title" content="{no_colon}">'), title=no_colon)[0]['title_present'])
        self.assertFalse(self.parse(html(f'<h1>{main}</h1><h1>{main}</h1>',
            f'<meta name="citation_title" content="{title}">'), title=title)[0]['title_present'])
        original = main + ': original trial report'
        self.assertFalse(self.parse(html(f'<h1>{main}</h1>',
            f'<meta name="citation_title" content="{title}">'), title=original)[0]['title_present'])

    def test_h2_fallback_is_only_the_first_main_heading(self):
        self.assertTrue(self.parse(html(f'<main><h2>{TARGET}</h2><p>Abstract</p></main>'))[0]['title_present'])
        for body in (f'<main><h2>Related articles</h2><h2>{TARGET}</h2></main>',
                     f'<main><section class="recommended"><h2>{TARGET}</h2></section></main>',
                     f'<main><h2><a href="/other">{TARGET}</a></h2></main>',
                     f'<h2>{TARGET}</h2>'):
            with self.subTest(body=body):
                self.assertFalse(self.parse(html(body))[0]['title_present'])

    def test_challenge_or_login_with_target_metadata_is_not_confirmed(self):
        for page_title in ('Just a moment...', 'Checking your browser', 'Access denied', 'Sign in'):
            with self.subTest(page_title=page_title):
                result, _ = self.parse(html('<p>Request cannot be completed.</p>',
                    f'<title>{page_title}</title><meta name="citation_title" content="{TARGET}">'))
                self.assertFalse(result['title_present'])
                self.assertEqual(result['page_state'], '访问验证或限制页面')

    def test_empty_expected_title_does_not_match(self):
        self.assertFalse(self.parse(html('<h1>A paper</h1>'), title='')[0]['title_present'])

    def test_cached_reclassification_retains_provenance_without_mutation(self):
        log = request_log()
        log.update(title_present=True, parse_error='obsolete', pdf_pages=99,
                   metadata={'citation_title': TARGET}, description='old description')
        before = copy.deepcopy(log)
        result, _ = self.parse(html(f'<h1>{OTHER}</h1><p>{TARGET}</p>'), log=log)
        self.assertEqual(log, before)
        for field in ('status', 'content_type', 'requested_url', 'final_url', 'sha256', 'checked_at', 'attempts', 'bytes'):
            self.assertEqual(result[field], before[field])
        self.assertFalse(result['title_present'])
        self.assertNotIn('parse_error', result)
        self.assertNotIn('pdf_pages', result)
        self.assertEqual(result['metadata'], {})
        self.assertEqual(result['page_parser_version'], harvest.PAGE_PARSER_VERSION)

    def test_failed_status_cannot_retain_old_success(self):
        for status in (None, 403, 429, 500):
            with self.subTest(status=status):
                log = request_log(status=status)
                log['title_present'] = True
                result, _ = self.parse(html(f'<h1>{TARGET}</h1>'), log=log)
                self.assertFalse(result['title_present'])
                self.assertEqual(result['page_state'], '受限或请求失败')

    def test_xml_primary_title_and_ids_exclude_references(self):
        content = f'''<article xmlns="urn:jats"><front><article-meta>
          <article-id pub-id-type="pmid">123</article-id>
          <title-group><article-title>{OTHER}</article-title></title-group>
          </article-meta></front><body><p>Substantive body</p></body>
          <back><ref-list><ref><article-id>456</article-id><article-title>{TARGET}</article-title>
          </ref></ref-list></back></article>'''.encode()
        result, _ = self.parse(content, 'application/xml')
        self.assertFalse(result['title_present'])
        self.assertEqual(result['ids'], ['123'])
        self.assertEqual(result['xml_title'], OTHER)
        self.assertTrue(result['body_present'])
        content = content.replace(OTHER.encode(), TARGET.encode())
        self.assertTrue(self.parse(content, 'application/xml')[0]['title_present'])
        record, _, ids = harvest._xml_main_record(ET.fromstring(content))
        self.assertEqual(ids, ['123'])
        self.assertIsNotNone(record.find('body'))

    def test_xml_missing_primary_title_or_multiple_records_remains_unconfirmed(self):
        for content in (f'<article><back><article-title>{TARGET}</article-title></back></article>',
                        f'<pmc-articleset><article><front><article-title>{TARGET}</article-title>'
                        '</front></article><article/></pmc-articleset>',
                        '<article><front>malformed'):
            with self.subTest(content=content):
                self.assertFalse(self.parse(content.encode(), 'application/xml')[0]['title_present'])

    def test_xml_combines_only_the_same_primary_title_group_subtitle(self):
        template = '''<article><front><article-meta><title-group>
          <article-title>Liver resection</article-title><subtitle>{subtitle}</subtitle>
          </title-group></article-meta></front><back><ref-list><ref><title-group>
          <article-title>Liver resection</article-title><subtitle>a randomized trial in 2025</subtitle>
          </title-group></ref></ref-list></back></article>'''
        for subtitle, expected in (('a randomized trial in 2025', True),
                                   ('a randomized trial in 2024', False),
                                   ('a randomized trial in 2025: follow-up report', False)):
            with self.subTest(subtitle=subtitle):
                result, _ = self.parse(template.format(subtitle=subtitle).encode(), 'application/xml')
                self.assertEqual(result['title_present'], expected)
                self.assertEqual(result['xml_title'], 'Liver resection: ' + subtitle)
        without_subtitle = template.replace('<subtitle>{subtitle}</subtitle>', '')
        self.assertFalse(self.parse(without_subtitle.encode(), 'application/xml')[0]['title_present'])

    def test_pdf_uses_first_page_title_block_and_keeps_full_extracted_text(self):
        content = pdf([(TARGET, 20, 700), ('Authors', 12, 640), ('Abstract text', 10, 580)],
                      [('References', 18, 700)])
        result, text = self.parse(content, 'application/pdf')
        self.assertTrue(result['title_present'])
        self.assertEqual(result['pdf_pages'], 2)
        self.assertIn('References', text)
        self.assertEqual(result['pdf_title_region']['page'], 1)

    def test_pdf_reference_on_first_or_second_page_cannot_confirm(self):
        for content in (pdf([(OTHER, 20, 700), (TARGET, 10, 560)]),
                        pdf([(OTHER, 20, 700)], [(TARGET, 20, 700)]),
                        pdf([('Other study', 20, 700), (TARGET, 20, 300)])):
            with self.subTest(bytes=len(content)):
                result, text = self.parse(content, 'application/pdf')
                self.assertIn(TARGET, text)
                self.assertFalse(result['title_present'])

    def test_identity_only_pdf_does_not_extract_later_pages(self):
        from pypdf import PdfReader
        from pypdf._page import PageObject
        content = pdf([(TARGET, 20, 700)], [('Later page body', 12, 700)])
        original = PageObject.extract_text
        visited = []

        def guarded(page, *args, **kwargs):
            self.assertIn('visitor_text', kwargs)
            visited.append(page)
            return original(page, *args, **kwargs)

        with patch.object(PageObject, 'extract_text', guarded):
            result, text = harvest.parse_page_content(
                content, TARGET, request_log('application/pdf'), extract_body=False)
        self.assertTrue(result['title_present'])
        self.assertEqual(len(visited), 1)
        self.assertEqual(text, TARGET)
        self.assertEqual(result['text_extraction_scope'], 'identity-only')

    def test_identity_only_html_and_xml_return_limited_text_with_same_identity(self):
        examples = [(html(f'<h1>{TARGET}</h1><p>' + 'Body text ' * 3000 + '</p>'), 'text/html'),
                    (f'<article><front><article-title>{TARGET}</article-title></front>'
                     '<body><p>Long body</p></body></article>'.encode(), 'application/xml')]
        for content, content_type in examples:
            with self.subTest(content_type=content_type):
                full, _ = self.parse(content, content_type)
                fast, text = harvest.parse_page_content(
                    content, TARGET, request_log(content_type), extract_body=False)
                self.assertEqual(fast['title_present'], full['title_present'])
                self.assertTrue(fast['title_present'])
                self.assertLessEqual(len(text), 600)
                self.assertEqual(fast['checked_at'], full['checked_at'])
                if content_type == 'application/xml':
                    self.assertIsNone(fast['body_chars'])

    def test_bad_pdf_and_xml_do_not_confirm(self):
        for raw, content_type in ((b'%PDF bad file', 'application/pdf'),
                                  (b'<article><front>', 'application/xml')):
            with self.subTest(content_type=content_type):
                result, _ = self.parse(raw, content_type)
                self.assertFalse(result['title_present'])
                self.assertIn('parse_error', result)

    def test_page_check_interface_and_cached_bytes_roundtrip(self):
        content = html(f'<h1>{TARGET}</h1>')
        response = SimpleNamespace(content=content, status_code=200)
        log = request_log()
        with tempfile.TemporaryDirectory() as temp, patch.object(harvest, 'WORK', Path(temp)), \
                patch.object(harvest, 'fetch', return_value=(response, log)):
            rid, result = harvest.page_check(('A', log['requested_url'], TARGET))
            self.assertEqual(rid, 'A')
            cache = Path(temp) / 'pages' / 'A.html'
            self.assertEqual(cache.read_bytes(), content)
            reparsed, text = harvest.parse_page_content(cache.read_bytes(), TARGET, result)
            self.assertEqual(result, reparsed)
            self.assertEqual((Path(temp) / 'pages' / 'A.txt').read_text(), text)


if __name__ == '__main__':
    unittest.main()
