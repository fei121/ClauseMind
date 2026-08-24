"""
Date: 2025-09-29 17:35:38
LastEditTime: 2025-09-29 17:46:34
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
import PyPDF2


def extract_links_pypdf2(pdf_path):
    """
    使用PyPDF2提取PDF中的超链接。

    参数:
        pdf_path (str): PDF文件的路径

    返回:
        list: 包含链接URL的列表
    """
    links = []
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)

        for page_num in range(len(reader.pages)):
            page = reader.pages[page_num]

            # 检查页面是否有注解
            if '/Annots' in page:
                for annot in page['/Annots']:
                    # 获取注解对象
                    annot_obj = annot.get_object()
                    # 判断是否为链接注解
                    if annot_obj.get('/Subtype') == '/Link':
                        # 获取链接动作（Action）和URI
                        action = annot_obj.get('/A')
                        if action:
                            action_obj = action.get_object()
                            uri = action_obj.get('/URI')
                            if uri and 'pdf' in uri:
                                links.append(uri)
                                print(f"Page {page_num + 1}: {uri}")
    return links

if __name__ == "__main__":
    # 使用示例
    pdf_file = "examples/demo_policy.pdf"
    link_list = extract_links_pypdf2(pdf_file)
    # print(link_list)
