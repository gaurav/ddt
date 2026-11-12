# Test fixtures

Excerpts of the two example datasets, cut as raw bytes so they keep the original encodings and
line endings (do not open and re-save them in an editor).

- `henderson_excerpt.csv`: 40 of 1,087 records from Appendix 3 of Thomer A, Vaidya G, Guralnick R,
  Bloom D, Russell L (2012) From documents to datasets: A MediaWiki-based method of annotating and
  extracting species observations in century-old field notebooks. ZooKeys 209: 235-253.
  https://doi.org/10.3897/zookeys.209.3247 . Mostly UTF-8 with a few Latin-1 bytes, CRLF line
  endings, duplicate header names.
- `cushman_excerpt.csv`: 42 of 15,190 records from the Charles W. Cushman Photograph Collection
  metadata, Indiana University Archives, https://github.com/iulibdcs/cushman_photos . Mac Roman,
  bare CR line endings.
