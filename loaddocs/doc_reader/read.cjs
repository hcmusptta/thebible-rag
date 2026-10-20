const WordExtractor = require('word-extractor');

// Parse document streams only: no Office process, macros, or linked objects run.
(async () => {
  const doc = await new WordExtractor().extract(process.argv[2]);
  const parts = {
    body: doc.getBody(),
    footnotes: doc.getFootnotes(),
    endnotes: doc.getEndnotes(),
    headers: doc.getHeaders({ includeFooters: false }),
    footers: doc.getFooters(),
    comments: doc.getAnnotations(),
    textboxes: doc.getTextboxes({ includeHeadersAndFooters: false })
  };
  if (Object.values(parts).reduce((n, text) => n + text.length, 0) > 20000000) {
    throw new Error('DOC exceeds the text size limit');
  }
  if (parts.textboxes === parts.body) parts.textboxes = '';
  process.stdout.write(JSON.stringify(parts));
})().catch(error => {
  process.stderr.write(String(error.message));
  process.exitCode = 1;
});
