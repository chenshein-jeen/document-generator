from docx import Document

doc = Document(r"/Users/chenshein/mafat-docgen/dani_template.docx")

# מציאת כל פקדי התוכן במסמך
sdt_elements = doc.element.xpath('//*[local-name()="sdt"]')

print(f"נמצאו {len(sdt_elements)} פקדי תוכן במסמך:\n" + "-"*40)

for idx, sdt in enumerate(sdt_elements, 1):
    # שליפת ה-Title / Alias
    alias_nodes = sdt.xpath('.//*[local-name()="alias"]/@*[local-name()="val"]')
    alias = alias_nodes[0] if alias_nodes else "ללא שם (Title)"

    # שליפת ה-Tag
    tag_nodes = sdt.xpath('.//*[local-name()="tag"]/@*[local-name()="val"]')
    tag = tag_nodes[0] if tag_nodes else "ללא Tag"

    # שליפת כל הטקסט שבתוך הפקד
    text_nodes = sdt.xpath('.//*[local-name()="t"]')
    current_text = "".join([node.text for node in text_nodes if node.text])

    print(f"פקד #{idx}:")
    print(f"  - Title (Alias): {alias}")
    print(f"  - Tag:           {tag}")
    print(f"  - טקסט נוכחי:    '{current_text}'")
    print("-" * 40)