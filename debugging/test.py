from docling.document_converter import DocumentConverter
import json

converter = DocumentConverter()
result = converter.convert("/Users/trishakumar/Documents/multimodal_rag/local/report_2022_pages_8-11.pdf")

jsondata = result.document.export_to_dict()
with open('result.json', 'w') as f:
    json.dump(jsondata, f)