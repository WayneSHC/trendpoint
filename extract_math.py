import zipfile
import xml.etree.ElementTree as ET

def extract_docx_text_with_math(docx_path):
    # Namespace mappings for docx
    namespaces = {
        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'
    }
    
    with zipfile.ZipFile(docx_path) as docx:
        tree = ET.parse(docx.open('word/document.xml'))
        root = tree.getroot()
        
        # We want to iterate through all paragraphs and math blocks in order
        body = root.find('w:body', namespaces)
        if body is None:
            return ""
        
        output = []
        
        # Helper function to recursively extract text from an element
        def get_text(elem):
            text_parts = []
            for child in elem.iter():
                if child.tag == f"{{{namespaces['w']}}}t":
                    text_parts.append(child.text or "")
                # Handle math text
                elif child.tag == f"{{{namespaces['m']}}}t":
                    text_parts.append(child.text or "")
            return "".join(text_parts)

        # Iterate over child elements of body
        for child in body:
            # Check for paragraphs
            if child.tag == f"{{{namespaces['w']}}}p":
                # Find math elements in the paragraph
                p_text = []
                for sub_child in child:
                    if sub_child.tag == f"{{{namespaces['m']}}}oMath":
                        # This is a math block
                        math_str = get_text(sub_child)
                        p_text.append(f" [Math: {math_str}] ")
                    elif sub_child.tag == f"{{{namespaces['m']}}}oMathPara":
                        # Math paragraph
                        math_str = get_text(sub_child)
                        p_text.append(f" [MathPara: {math_str}] ")
                    else:
                        p_text.append(get_text(sub_child))
                output.append("".join(p_text))
            elif child.tag == f"{{{namespaces['m']}}}oMathPara":
                output.append(f" [MathPara: {get_text(child)}] ")
            elif child.tag == f"{{{namespaces['w']}}}tbl":
                # Table handling
                for row in child.findall('w:tr', namespaces):
                    row_text = []
                    for cell in row.findall('w:tc', namespaces):
                        cell_paragraphs = []
                        for cp in cell.findall('w:p', namespaces):
                            cell_paragraphs.append(get_text(cp))
                        row_text.append(" | ".join(cell_paragraphs))
                    output.append("Table Row: " + " || ".join(row_text))
        
        return "\n".join(output)

if __name__ == '__main__':
    text = extract_docx_text_with_math('多空階梯優化與實戰策略.docx')
    with open('多空階梯優化與實戰策略_extracted.txt', 'w', encoding='utf-8') as f:
        f.write(text)
    print("Extraction complete!")
