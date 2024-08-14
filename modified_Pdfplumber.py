# -*- coding:utf-8 -*-
import re, sys, logging, json
import pdfplumber, numpy, decimal
import pandas as pd

# The approximate width of an English character
# The height of Chinese and English characters is roughly 10. Some characters are not exactly the same height as the text,
# such as commas, and a tolerance range is needed.
y_tolerance = 2
x_tolerance = 5

# Minimum number of lines for left and right grouping
min_group_len = 5
# The approximate height/width of Chinese and English characters in normal font size
pdf_char_width = 10
pdf_char_height = 10
pdf_line_height = pdf_char_height + 10

# Some financial reports have left-right pagination and need to be processed separately
def PageInGroups(pg, word_rows):
    center = pg.width/2
    # Remove rows where the text crosses the center line
    def filter_cross_center(row):
        valid = True
        for x in row:
            if x['x1'] > center and x['x0'] < center:
                valid = False
                break
        return valid
    rows = filter(filter_cross_center, word_rows)

    # Split left and right
    left_lines = []
    right_lines = []
    for row in rows:
        left_row = []
        right_row = []
        for w in row:
            if w['x1'] < center:
                left_row.append(w)
            if w['x0'] > center:
                right_row.append(w)
        # print(center, len(left_row), len(right_row), row)
        if len(left_row) > 0:
            left_lines.append(left_row)
        if len(right_row) > 0:
            right_lines.append(right_row)

    # A PDF page that is split left and right will inevitably show that the right boundary of the left page is almost aligned
    # And the left boundary of the right page is also aligned
    # This method can be used to avoid misjudging some pages that do not have left-right pagination as having left-right pagination.
    if len(left_lines) > 0:
        def right_edge(line):
            return line[-1]['x1']
        leftpage_rightedges = list(map(right_edge, left_lines))
        # Median
        median_right = numpy.median(leftpage_rightedges)
        if center - median_right > pdf_char_width * 2:
            # print(center, median_right, pdf_char_width)
            return [], []

    if len(right_lines) > 0:
        def left_edge(line):
            return line[0]['x0']
        rightpage_leftedges = list(map(left_edge, right_lines))
        median_left = numpy.median(rightpage_leftedges)
        if center - median_left > pdf_char_width * 2:
            # print(center, median_left, pdf_char_width)
            return [], []

    return left_lines, right_lines

# Some cells have text that is too long and has been broken, try to fix it.
def merge_cross_line(rows):
    pass

def extract_tables(rows):
    # If there are multiple tables on a page, they must be structurally consistent to be parsed. Otherwise, an error will occur.
    # The common number of columns that most rows have

    # Filter out rows that have only one word
    def filter_dismatch_row(row):
        return len(row) > 1
    rows = list(filter(filter_dismatch_row, rows))

    # Determine if two different cells of a row have overlapping areas
    def overlap(cell0, cell1):
        if cell0['x0'] > cell1['x0']:
            cell0, cell1 = cell1, cell0

        return cell1['x0'] < cell0['x1']

    # Determine if two rows belong to the same table.
    def similar_struct(row0, row1):
        # The difference in row positions is too large, considering some text folding issues, the tolerance needs to be larger
        if abs(row0[0]['bottom'] - row1[0]['bottom']) > pdf_line_height * 2:
            return False

        # The table structure is too different, it is judged as a new table
        if abs(len(row0) - len(row1)) > 1:
            return False

        # 
        if len(row0) > len(row1):
            row0, row1 = row1, row0

        found = 0
        for i in range(len(row0)):
            for j in range(len(row1)):
                c0 = row0[i]
                c1 = row1[j]
                # print("c0", c0)
                # print("c1", c1)
                if overlap(c0, c1):
                    found += 1
                    # c0 and c1 overlap, and also overlap with the next column, which means it cannot be aligned by column
                    # This must not be the same table
                    if j != len(row1) - 1 and overlap(c0, row1[j + 1]):
                        return False
        if found == len(row0):
            return True
        else:
            return False

    def year_row(row):
        for w in row:
            if re.search("\d{4}年", w['text']):
                return True
        return False

    def year_merged(row0, row1):
        merged = False
        for i in range(len(row0)):
            w0 = row0[i]
            for j in range(len(row1)):
                w1 = row1[j]
                if re.match('\d+月\d+日', w1['text']) or \
                    re.match('第\w+季度', w1['text']):
                    if abs(w0['x1'] - w1['x1']) < x_tolerance or \
                        abs(w0['x0'] - w1['x0']) < x_tolerance:
                        w1['text'] = w0['text'] + w1['text']
                        row1[j] = w1
                        merged = True
        return merged, row1

    tables = []
    table = []
    for row in rows:
        if len(table) == 0:
            table.append(row)
        else:
            if len(table) == 1 and year_row(table[-1]):
                merged, new_row = year_merged(table[-1], row)
                # print(merged, new_row)
                if merged:
                    table[-1] = new_row
                    continue
            if similar_struct(table[-1], row):    
                table.append(row)
            else:
                if len(table) > 1:
                    tables.append(table)
                table = [row]
    if len(table) > 1:
        tables.append(table)

    # Fill in empty data for rows with a similar structure but missing data, such as the notes column in financial reports.
    def align_table(table):
        max_fields_row = max(table, key=lambda x: len(x)).copy()
        min_fields_row = min(table, key=lambda x: len(x)).copy()
        if len(max_fields_row) == len(min_fields_row):
            return table

        # print(min_fields_row)
        # print(max_fields_row)
        # Some table structures are abnormal, remove them
        if len(max_fields_row) - len(min_fields_row) > 1:
            def filter_abnormal(row):
                return len(max_fields_row) - len(row) <= 1
            table = list(filter(filter_abnormal, table))

        def _align(row):
            if len(row) == len(min_fields_row):
                for i in range(len(max_fields_row)):
                    found = False
                    for j in range(len(row)):
                        if overlap(max_fields_row[i], row[j]): 
                            found = True
                    if not found:
                        cell = max_fields_row[i].copy()
                        cell['text'] = ''
                        row.insert(i, cell)
                        return row
                # print(row)
                # print(min_fields_row)
                # print(max_fields_row)
            else:
                return row

        # print(table)
        table = list(map(_align, table))
        return table

    def get_texts(row):
        return list(map(lambda x: x['text'], row))

    def get_table_texts(table):
        assert (table)
        table = align_table(table)
        return list(map(get_texts, table))
    tables = list(map(get_table_texts, tables))

    return tables

def ExtractPageTables(pg):

    # print("pg ", pg.width, pg.height)

    # Set bounding box, which will cause inconsistencies between page.width and coordinates
    # bbox = (40,30,560,800)
    # pg = pg.within_bbox(bbox)

    words = pg.extract_words(x_tolerance=x_tolerance, y_tolerance=y_tolerance)
    # PingAN(words)

    # Some financial reports have vertical text on the side, which needs to be filtered out, otherwise it will affect the analysis of the table.
    def filter_chars(obj):
        if obj['object_type'] == 'char':
            if obj['upright'] == 0:
                return False
            # Do not filter out at the character stage, otherwise it will be miscalculated when connecting words
            # if re.match('\(cid:\d+\)', obj['text']):
            #    return False
        return True
        
    filtered = pg.filter(filter_chars)
    words = list(filtered.extract_words(x_tolerance=x_tolerance, y_tolerance=y_tolerance))
    # Some financial reports use special fonts that cannot be recognized and need to be filtered out
    def filter_cid(obj):
        return not re.search('\(cid:\d+\)', obj['text'])

    words = filter(filter_cid, words)

    # def filter_long_words(obj):
    #    return obj['x1'] - obj['x0'] < pg.width/2 
    # words = filter(filter_long_words, words)

    # Use the bottom position information of words to find potential rows
    words = sorted(words, key=lambda x: x['bottom'])
    rows = []
    row = []
    for word in words:
        # print(word)
        if len(row) == 0:
            # first row
            row.append(word)
        else:
            if abs(row[-1]['bottom'] - word['bottom']) <= y_tolerance:
                row.append(word)
            else:
                # row = sorted(row, key=lambda x: x['x0'])
                rows.append(row)
                row = [word]  # new row
    rows.append(row)

    def sort_row(row):
        row = sorted(row, key=lambda x: x['x0'])
        return row

    rows = map(sort_row, rows)

    # The returned data sometimes contains errors, such as the end of the previous word overlapping with the beginning of the next word
    # Or it should have been merged but was not, these data need to be corrected
    def concat_words(row):
        new_row = []
        for i in range(len(row)):
            if i != 0 and (row[i]['x0'] - row[i - 1]['x1']) < x_tolerance:
                word = new_row[-1]
                word['x1'] = row[i]['x1']
                word['text'] += row[i]['text']
                new_row[-1] = word
            else:
                new_row.append(row[i])
        return new_row

    rows = list(map(concat_words, rows))

    left_groups, right_groups = PageInGroups(pg, rows)
    if len(left_groups) > 0 or len(right_groups) > 0:
        # print("groups", len(left_groups), len(right_groups))
        return extract_tables(left_groups) + extract_tables(right_groups)
    else:
        return extract_tables(rows)

def ExtractPDFtables(f):
    pdf = pdfplumber.open(f)
    tables = {}
    print("total pages:", len(pdf.pages))
    for i in range(len(pdf.pages)):
        pg = pdf.pages[i]
        #print("extract page:", i)
        new_tables = ExtractPageTables(pg)
        tables[i] = new_tables


    return tables
def ExtractPDFByPage(f, page_id):
    pdf = pdfplumber.open(f)
    pg = pdf.pages[i]
    return {i:ExtractPageTables(pg)}




#TestBonusFile()
if __name__ == "__main__":
    logging.getLogger().setLevel(logging.WARN)
    args = len(sys.argv)
    tables = {}
    if  args <= 1:
        print("require pdf pathfile...")
    elif args == 2:
        tables = ExtractPDFtables(sys.argv[1])
    else :
        page_id = int(sys.argv[2])
        tables = ExtractPDFByPage(sys.argv[1], page_id)

    #print(json.dumps(tables,indent=1,ensure_ascii=False))