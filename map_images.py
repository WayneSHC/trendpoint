# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import zipfile
import xml.etree.ElementTree as ET
import os

def map_images_to_text(docx_path):
    namespaces = {
        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture'
    }
    
    # 1. Read relationships
    rels = {}
    with zipfile.ZipFile(docx_path) as docx:
        try:
            rels_tree = ET.parse(docx.open('word/_rels/document.xml.rels'))
            for rel in rels_tree.getroot().findall('{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'):
                rels[rel.get('Id')] = rel.get('Target')
        except KeyError:
            pass
            
        # Create output directory for images
        os.makedirs('extracted_images', exist_ok=True)
        
        # 2. Parse document XML
        tree = ET.parse(docx.open('word/document.xml'))
        root = tree.getroot()
        body = root.find('w:body', namespaces)
        
        image_mappings = []
        
        def get_text(elem):
            return "".join(t.text or "" for t in elem.iter(f"{{{namespaces['w']}}}t"))
            
        for p in body.findall('w:p', namespaces):
            p_text = get_text(p)
            # Look for drawings
            for drawing in p.iter(f"{{{namespaces['w']}}}drawing"):
                for blip in drawing.iter(f"{{{namespaces['a']}}}blip"):
                    r_id = blip.get(f"{{{namespaces['r']}}}embed")
                    if r_id in rels:
                        target = rels[r_id]
                        filename = os.path.basename(target)
                        # Extract the image
                        try:
                            img_data = docx.read(f"word/{target}")
                            with open(f"extracted_images/{filename}", 'wb') as img_file:
                                img_file.write(img_data)
                            image_mappings.append((filename, p_text))
                        except Exception as e:
                            print(f"Error extracting {filename}: {e}")
                            
    return image_mappings

if __name__ == '__main__':
    mappings = map_images_to_text('多空階梯優化與實戰策略.docx')
    for img, text in mappings:
        print(f"Image: {img} | Paragraph text: {text[:150]}")
