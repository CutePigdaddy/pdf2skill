
# Python API documentation for http://localhost:7860/
API Endpoints: 1

1. Install the Python client [docs](https://www.gradio.app/guides/getting-started-with-the-python-client) if you don't already have it installed. 

```bash
pip install gradio_client
```

2. Find the API endpoint below corresponding to your desired function in the app. Copy the code snippet, replacing the placeholder values with your own input data. 

### API Name: /convert_to_markdown_stream


```python
from gradio_client import Client, handle_file

client = Client("http://localhost:7860")
result = client.predict(
	file_path=handle_file('https://github.com/gradio-app/gradio/raw/main/test/test_files/sample_file.pdf'),
	end_pages=1000,
	is_ocr=False,
	formula_enable=True,
	table_enable=True,
	language="ch (Chinese, English, Chinese Traditional)",
	backend="hybrid-auto-engine",
	url="http://localhost:30000",
	api_name="/convert_to_markdown_stream",
)
print(result)
```

Accepts 8 parameters:

file_path:
- Type: filepath
- Required
- The input value that is provided in the [object Object] File component. The FileData class is a subclass of the GradioModel class that represents a file object within a Gradio interface. It is used to store file data and metadata when a file is uploaded.

Attributes:
    path: The server file path where the file is stored.
    url: The normalized server URL pointing to the file.
    size: The size of the file in bytes.
    orig_name: The original filename before upload.
    mime_type: The MIME type of the file.
    is_stream: Indicates whether the file is a stream.
    meta: Additional metadata used internally (should not be changed).

end_pages:
- Type: float
- Default: 1000
- The input value that is provided in the [object Object] Slider component. 

is_ocr:
- Type: bool
- Default: False
- The input value that is provided in the [object Object] Checkbox component. 

formula_enable:
- Type: bool
- Default: True
- The input value that is provided in the [object Object] Checkbox component. 

table_enable:
- Type: bool
- Default: True
- The input value that is provided in the [object Object] Checkbox component. 

language:
- Type: Literal['ch (Chinese, English, Chinese Traditional)', 'ch_lite (Chinese, English, Chinese Traditional, Japanese)', 'ch_server (Chinese, English, Chinese Traditional, Japanese)', 'en (English)', 'korean (Korean, English)', 'japan (Chinese, English, Chinese Traditional, Japanese)', 'chinese_cht (Chinese, English, Chinese Traditional, Japanese)', 'ta (Tamil, English)', 'te (Telugu, English)', 'ka (Kannada)', 'el (Greek, English)', 'th (Thai, English)', 'latin (French, German, Afrikaans, Italian, Spanish, Bosnian, Portuguese, Czech, Welsh, Danish, Estonian, Irish, Croatian, Uzbek, Hungarian, Serbian (Latin), Indonesian, Occitan, Icelandic, Lithuanian, Maori, Malay, Dutch, Norwegian, Polish, Slovak, Slovenian, Albanian, Swedish, Swahili, Tagalog, Turkish, Latin, Azerbaijani, Kurdish, Latvian, Maltese, Pali, Romanian, Vietnamese, Finnish, Basque, Galician, Luxembourgish, Romansh, Catalan, Quechua)', 'arabic (Arabic, Persian, Uyghur, Urdu, Pashto, Kurdish, Sindhi, Balochi, English)', 'east_slavic (Russian, Belarusian, Ukrainian, English)', 'cyrillic (Russian, Belarusian, Ukrainian, Serbian (Cyrillic), Bulgarian, Mongolian, Abkhazian, Adyghe, Kabardian, Avar, Dargin, Ingush, Chechen, Lak, Lezgin, Tabasaran, Kazakh, Kyrgyz, Tajik, Macedonian, Tatar, Chuvash, Bashkir, Malian, Moldovan, Udmurt, Komi, Ossetian, Buryat, Kalmyk, Tuvan, Sakha, Karakalpak, English)', 'devanagari (Hindi, Marathi, Nepali, Bihari, Maithili, Angika, Bhojpuri, Magahi, Santali, Newari, Konkani, Sanskrit, Haryanvi, English)']
- Default: "ch (Chinese, English, Chinese Traditional)"
- The input value that is provided in the [object Object] Dropdown component. 

backend:
- Type: Literal['pipeline', 'vlm-auto-engine', 'hybrid-auto-engine']
- Default: "hybrid-auto-engine"
- The input value that is provided in the [object Object] Dropdown component. 

url:
- Type: str
- Default: "http://localhost:30000"
- The input value that is provided in the [object Object] Textbox component. 

Returns tuple of 5 elements:

[0]: - Type: str
- The output value that appears in the "[object Object]" Textbox component.

[1]: - Type: filepath
- The output value that appears in the "[object Object]" File component.

[2]: - Type: str
- The output value that appears in the "[object Object]" Markdown component.

[3]: - Type: str
- The output value that appears in the "[object Object]" Textbox component.

[4]: - Type: filepath
- The output value that appears in the "doc preview" Pdf component.

