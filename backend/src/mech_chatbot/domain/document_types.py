"""Canonical file extensions accepted as internal knowledge documents."""

PDF_EXTENSIONS = {".pdf"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
TEXT_EXTENSIONS = MARKDOWN_EXTENSIONS | {
    ".txt", ".rst", ".log", ".sql",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cs",
    ".cpp", ".c", ".h", ".hpp", ".json", ".xml", ".yaml",
    ".yml", ".ini", ".cfg",
}
HTML_EXTENSIONS = {".html", ".htm"}
TABLE_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls"}
WORD_EXTENSIONS = {".docx"}
PRESENTATION_EXTENSIONS = {".pptx"}
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff",
}
SUPPORTED_LEARNING_EXTENSIONS = (
    PDF_EXTENSIONS
    | TEXT_EXTENSIONS
    | HTML_EXTENSIONS
    | TABLE_EXTENSIONS
    | WORD_EXTENSIONS
    | PRESENTATION_EXTENSIONS
    | IMAGE_EXTENSIONS
)
