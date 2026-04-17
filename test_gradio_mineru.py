import os
import sys
import shutil
import zipfile
from pathlib import Path

# Ensure gradio_client is available
try:
    from gradio_client import Client, handle_file
except ImportError:
    print("gradio_client not found. Installing...")
    os.system(f"{sys.executable} -m pip install gradio_client")
    from gradio_client import Client, handle_file

def test_mineru_gradio():
    # Use DLCO.pdf in the workspace root
    pdf_path = "DLCO.pdf"
    if not os.path.exists(pdf_path):
        print(f"Cannot find {pdf_path}. Please make sure it is in the current directory.")
        return

    print("Connecting to Gradio API at http://localhost:7860/ ...")
    try:
        client = Client("http://localhost:7860/")
    except Exception as e:
        print(f"Failed to connect to Gradio server: {e}")
        return

    print(f"\nSubmitting {pdf_path} (first 20 pages) for parsing via Gradio API...")
    try:
        result = client.predict(
            file_path=handle_file(pdf_path),
            end_pages=20,  # 仅测试前 20 页
            is_ocr=False,
            formula_enable=True,
            table_enable=True,
            language="ch (Chinese, English, Chinese Traditional)",
            backend="hybrid-auto-engine",
            url="http://localhost:30000",
            api_name="/convert_to_markdown_stream",
        )
        print("\nPrediction completed! Processing results...")

        # Gradio 端点通常返回的 tuple [1] 是文件的临时路径（ZIP格式）
        output_zip_path = result[1]
        
        if output_zip_path and os.path.exists(output_zip_path):
            out_dir = Path("test_gradio_output")
            out_dir.mkdir(parents=True, exist_ok=True)
            
            # 将 Gradio 客户端缓存的 ZIP 复制到我们当前目录下的独立文件夹
            local_zip = out_dir / "dlco_test_output.zip"
            shutil.copy2(output_zip_path, local_zip)
            print(f"Saved ZIP output down to: {local_zip}")
            
            # 解压并观察图片提取情况
            with zipfile.ZipFile(local_zip, 'r') as zf:
                zf.extractall(out_dir)
                
            images = list(out_dir.rglob("images/*"))
            md_files = list(out_dir.rglob("*.md"))
            
            print("\n------------------------------")
            print("Extraction Review:")
            print(f"- Found {len(md_files)} markdown files.")
            for md in md_files:
                print(f"  - {md.name} (Size: {os.path.getsize(md)} bytes)")
            print(f"- Found {len(images)} images in the extracted output.")
            if len(images) > 0:
                print("  => SUCCESS! The Gradio API returned images!")
            else:
                print("  => FAILED! STILL NO IMAGES found in the returned ZIP file from Gradio API.")
                
            print("\nDirectory contents of test_gradio_output:")
            for item in out_dir.rglob("*"):
                if item.is_file():
                    print(f"  {item.relative_to(out_dir)}")
        else:
            print("The API did not return a valid file path in result[1].")
            print("Raw API result:", result)
            
    except Exception as e:
        print(f"\nError during API call: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_mineru_gradio()