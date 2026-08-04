# Derived from extract.py by Phani Chaitanya Pendyala, published at
# https://github.com/chaitupendyala/inmagic-project under the MIT License.
# This file retains structure, identifiers, and the month/season chronology
# code mapping from that script. See THIRD-PARTY-NOTICES.md.

from pymarc import MARCReader, Field, Subfield, Indicators
from tkinter.filedialog import askopenfilename
import re
import argparse
from pathlib import Path
from copy import deepcopy

from pattern_manager import get_default_pattern, load_pattern, pattern_exists, save_pattern
from sample_collector import collect_holdings_samples
from regex_validator import generate_validation_report
from ai_regex_generator import generate_regex_from_samples


def parse_holdings(holdings_statement, collection_id=None):
    """Function to parse holdings statement using regex.
    Uses collection-specific pattern if collection_id is set and a pattern exists."""
    if collection_id and pattern_exists(collection_id):
        loaded = load_pattern(collection_id)
        if loaded:
            year_pattern = loaded["year_re"]
            chron_pattern = loaded["chron_re"]
        else:
            pat = get_default_pattern()
            year_pattern, chron_pattern = pat["year_re"], pat["chron_re"]
    else:
        pat = get_default_pattern()
        year_pattern, chron_pattern = pat["year_re"], pat["chron_re"]
    # Extract volume, issue, and chronology using regex; holdings format for further parsing
    try:
        matches = year_pattern.findall(holdings_statement)
        holdings_format = len(chron_pattern.findall(holdings_statement))
    except (TypeError, re.error):
        matches = []
        holdings_format = 0
    parsed_data = [
        {
            "vol1_no_issues": match[0], "vol2_no_issues": match[1],
            "vol1": match[2], "iss1": match[3],
            "vol2": match[4], "iss2": match[5],
            "chron": match[6]
        }
        for match in matches
    ]
    return parsed_data, holdings_format


def extract_adjacent(parsed_data):
    """Returns vol, iss, chron from parsed data if in adjacent format."""
    data = parsed_data[0]
    volume = (data.get('vol1', ''), data.get('vol2', ''))
    uncompressed = False
    if '-' in data.get('iss1'):
        issue = tuple(data.get('iss1').split('-'))
    else:
        issue = (data.get('iss1', '1'), data.get('iss2', ''))
    chronology = data.get('chron', '')
    if '-' in chronology:
        pass
    else:
        if volume[0] or issue[0]:
            if volume[1] == '' and issue[1] == '':
                uncompressed = True
    return volume, issue, chronology, uncompressed


def extract_adjacent_no_iss(parsed_data):
    """Returns vol, iss, chron from parsed data when no_iss and in adjacent format."""
    data_0 = parsed_data[0]
    volume = (data_0.get('vol1_no_issues', ''), data_0.get('vol2_no_issues', ''))
    issue = ('', parsed_data[1].get('iss1', ''))
    chronology = parsed_data[1].get('chron', '')
    return volume, issue, chronology


def extract_separate(parsed_data):
    """Returns vol, iss, chron from parsed data when in separate format."""
    data_0 = parsed_data[0]
    data_1 = parsed_data[1]
    if not data_0.get('vol1') and data_0.get('iss1') and data_1.get('iss1'):
        volume = ('', '')
        issue = (data_0.get('iss1', ''), data_1.get('iss1', ''))
    elif data_1.get('vol1'):
        volume = (data_0.get('vol1', ''), data_1.get('vol1', ''))
        issue = ('', data_1.get('iss1', ''))
    else:
        volume = (data_0.get('vol1', ''), data_1.get('vol2', ''))
        issue = (data_0.get('iss1', ''), data_1.get('iss2', ''))
    chronology = f"{data_0.get('chron', '')}-{data_1.get('chron', '')}"
    return volume, issue, chronology


def extract_chron(chron):
    """Returns year and encoded month/season from chronology statement as a list."""
    # Define the MARC 853 encoding for months and seasons
    marc_853_encoding = {
        'Jan': '01', 'January': '01',
        'Feb': '02', 'February': '02',
        'Mar': '03', 'March': '03',
        'Apr': '04', 'April': '04',
        'May': '05',
        'Jun': '06', 'June': '06',
        'Jul': '07', 'July': '07',
        'Aug': '08', 'August': '08',
        'Sep': '09', 'September': '09',
        'Oct': '10', 'October': '10',
        'Nov': '11', 'November': '11',
        'Dec': '12', 'December': '12',
        'Spring': '21',
        'Summer': '22',
        'Autumn': '23', 'Fall': '23',
        'Winter': '24'
    }

    # Regex patterns to match month/season and year
    non_split_pattern = re.compile(
        r"(\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
        r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Autumn|Fall|Winter)?\b)?\s*(\d{4})?\s*(?:-\s*(\b"
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct"
        r"(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Autumn|Fall|Winter)?\b)?\s*(\d{4}))?"
    )

    def encode_dates(date_string):
        split_pattern = re.compile(r'(\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Autumn|Fall|Winter)(?:/\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Autumn|Fall|Winter))*)?\s*(\d{4})?(?:\s*-\s*(\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Autumn|Fall|Winter)(?:/\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Spring|Summer|Autumn|Fall|Winter))*)?\s*(\d{4}))?')
        match = re.match(split_pattern, date_string)
        if match:
            start_months, start_year, end_months, end_year = match.groups(default='')
            start_months_list = start_months.split('/') if start_months else ['']
            end_months_list = end_months.split('/') if end_months else ['']
            start_months_encoded = '/'.join(marc_853_encoding.get(month, '') for month in start_months_list)
            end_months_encoded = '/'.join(marc_853_encoding.get(month, '') for month in end_months_list) if end_months else ''
            return start_months_encoded, start_year, end_months_encoded, end_year
        return None

    matches = non_split_pattern.findall(chron)
    encoded_dates = []

    for match in matches:
        start_month, start_year, end_month, end_year = match
        encoded_start_month = marc_853_encoding.get(start_month, '') if start_month else ''
        encoded_end_month = marc_853_encoding.get(end_month, '') if end_month else ''
        encoded_dates.append((encoded_start_month, start_year, encoded_end_month, end_year))

    # Check for split dates and add their encoded dates
    if '/' in chron:
        split_encoded_dates = encode_dates(chron)
        if split_encoded_dates:
            encoded_dates = [split_encoded_dates]
    return encoded_dates


def read_file(filename, collection_id=None):
    """Read & return MARC records from a file. Optional collection_id for AI-generated patterns."""
    marc_records = None
    try:
        print(f"Entering read_file(filename) with the following filename {filename}")
        with open(filename, "rb") as f:
            file = MARCReader(f)
            marc_records = parse_file(file, collection_id=collection_id)
    except FileNotFoundError:
        print(f"File '{filename}' not found.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        print(f"Exiting read_file(filename) method with the following file {filename}")
    return marc_records


# Function to create a new MARC file from a list of records
def create_new_file(records, file_name):
    my_new_marc_filename = "my_new_marc_file.txt" if file_name is None else file_name
    with open(my_new_marc_filename, "w", encoding="utf-8") as data:
        for record in records:
            data.write(str(record) + "\n\n")


def detect_and_generate_pattern(marc_file, collection_id):
    """
    Collect samples from marc_file, ask AI to generate regex, validate, and optionally save.
    Returns True if a pattern was approved and saved, False otherwise.
    """
    print(f"Collecting holdings samples from {marc_file}...")
    samples = collect_holdings_samples(marc_file, max_samples=50)
    if not samples:
        print("No 866 $a holdings found in file.")
        return False
    print(f"Collected {len(samples)} samples. Requesting regex from OpenAI...")
    result = generate_regex_from_samples(samples)
    if not result["success"]:
        print(f"AI regex generation failed: {result['error']}")
        return False
    year_pattern = result["year_pattern"]
    chron_pattern = result["chron_pattern"]
    report = generate_validation_report(year_pattern, samples, chron_pattern=chron_pattern)
    print(report["report_text"])
    print("\nApprove and save this pattern for collection_id {!r}? [y/N] ".format(collection_id), end="")
    try:
        choice = input().strip().lower()
    except EOFError:
        choice = "n"
    if choice == "y":
        save_pattern(
            collection_id,
            year_pattern,
            chron_pattern=chron_pattern,
            metadata={"validation_stats": {"success_rate": report["success_rate"], "passed": report["passed"], "total": report["total"]}},
        )
        print(f"Pattern saved for collection {collection_id!r}.")
        return True
    print("Pattern not saved.")
    return False


def analyze_collection_pattern(marc_file, collection_id):
    """
    Pre-analyze collection: if no pattern exists for collection_id, run detect_and_generate_pattern.
    """
    if pattern_exists(collection_id):
        print(f"Using existing pattern for collection {collection_id!r}.")
        return
    print(f"No pattern found for collection {collection_id!r}. Running AI analysis...")
    detect_and_generate_pattern(marc_file, collection_id)


# noinspection PyUnboundLocalVariable
def parse_file(marc_reader_object, collection_id=None):
    """Iterates over each record in marc_reader_object.
    Yields tuples of vol, iss, chronology from 866 fields.
    Generates 853 and 863 fields and writes to MARC file.
    Uses collection_id to select AI-generated regex pattern if available.
    """
    records = []
    print(f"Entering parseFile(marcReaderObject) method with marcReaderObject {marc_reader_object}")
    new_007_field = Field(tag='007', data='ta')
    for i, record in enumerate(marc_reader_object, 1):
        if record is None:
            continue
        new_record = deepcopy(record)
        print('')
        print('MMSID:', new_record['999']['b'], '| Record No.:', i)

        # For debugging specific records
        if i == 695:  # Enter record number to debug
            print('STARTING DEBUG')

        if '866' in new_record and '863' not in new_record:
            my_866s = new_record.get_fields('866')
            c = 1
            for my_866 in my_866s:
                my_866_subfields = my_866.get_subfields('a')
                for my_866_subfield in my_866_subfields:
                    # formatted_text = []
                    contain_volume = False
                    contain_issue_number = False
                    contain_issue_month = False
                    contain_issue_season = False
                    uncompressed = False

                    parsed_data, holdings_format = parse_holdings(my_866_subfield, collection_id=collection_id)

                    # Print to console for debugging
                    # print(parsed_data, f'Holdings Format: {holdings_format}', sep=' - ')

                    if parsed_data[0]['vol1_no_issues']:
                        volume, issue, chronology = extract_adjacent_no_iss(parsed_data)
                    elif holdings_format == 1:
                        volume, issue, chronology, uncompressed = extract_adjacent(parsed_data)
                    elif holdings_format == 2:
                        volume, issue, chronology = extract_separate(parsed_data)
                    else:
                        print('No parsing match')
                        continue

                    # Print to console for debugging
                    # print(f'866 $a: {my_866_subfield}')
                    print(f'Extracted Values =  Volume: {volume}', f'Issue: {issue}', f'Chronology: {chronology}',
                          f'Record No.: {i}', sep=' | ')

                    # Call function to extract MARC encoded month/seasons and year from chronology
                    chron_string = extract_chron(chronology)

                    # Print to console for debugging
                    print(f'Encoded Chronology: {chron_string}')

                    issues_text = []
                    if volume[0] or volume[1]:
                        contain_volume = True
                    if issue[0] or issue[1]:
                        contain_issue_number = True
                    if chron_string[0][0] or chron_string[0][2]:
                        contain_issue_month = True
                        seas = ['21', '22', '23', '24']
                        for x in seas:
                            if chron_string[0][0] == x or chron_string[0][2] == x:
                                contain_issue_season = True
                    year = chron_string[0][1], chron_string[0][3]
                    issue_month = chron_string[0][0], chron_string[0][2]
                    issues_text.append(year)
                    issues_text.append(volume)
                    issues_text.append(issue)
                    issues_text.append(issue_month)
                    print(f'issues_text:        {issues_text};')


                    # Create function to generate 863 field(s?)
                    # Create function to generate 853 field

                    year, volume, issue_number, issue_month = issues_text

                    my_year = None
                    my_volume = None
                    my_issue_number = None
                    my_issue_month = None

                    if not year and not volume and not issue_number and not issue_month:
                        continue
                    # iss_range = False
                    # if issue_number[0] and issue_number[1]:
                    #     iss_range = True
                    if uncompressed:
                        indicators = Indicators('4', '1')
                    else:
                        indicators = Indicators('4', '0')
                    new_863_field = Field(
                        tag='863', indicators=indicators,

                        subfields=[Subfield(code='8', value=f' 1.{c} ')]
                    )
                    cur = 'a'
                    if contain_volume:
                        if volume[1] and volume[0] != volume[1]:
                            my_volume = f'{volume[0]}-{volume[1]}'
                        else:
                            my_volume = volume[0]
                        new_863_field.subfields.append(Subfield(code=cur, value=f' {my_volume} '))
                        cur = chr(ord(cur) + 1)
                    if contain_issue_number:
                        if issue_number[0] and issue_number[1]:
                            my_issue_number = f'{issue_number[0]}-{issue_number[1]}'
                        elif issue_number[0] and not issue_number[1]:
                            my_issue_number = issue_number[0]
                            # my_issue_number = ''
                        elif issue_number[1] and not issue_number[0]:
                            # my_issue_number = issue_number[1]
                            my_issue_number = ''
                        else:
                            print('check error - 1')
                        if my_issue_number:
                            new_863_field.subfields.append(Subfield(code=cur, value=f' {my_issue_number} '))
                    if year[0] and year[1]:
                        if year[0] != year[1]:
                            my_year = f'{year[0]}-{year[1]}'
                        else:
                            my_year = year[0]
                    elif year[0] and not year[1]:
                        my_year = year[0]
                    elif year[1] and not year[0]:
                        my_year = year[1]
                    else:
                        print('check error - 2')
                        # continue
                    new_863_field.subfields.append(Subfield(code='i', value=f' {my_year} '))
                    if contain_issue_month:
                        # if volume[0] == volume[1]:
                        if issue_month[0] and issue_month[1]:
                            my_issue_month = f'{issue_month[0]}-{issue_month[1]}'
                        elif issue_month[0] and not issue_month[1]:
                            my_issue_month = issue_month[0]
                            # my_issue_month = ''
                        elif issue_month[1] and not issue_month[0]:
                            # my_issue_month = issue_month[1]
                            my_issue_month = ''
                        else:
                            print('check error - 3')
                        # else:
                        #     my_issue_month = ''
                        if my_issue_month:
                            new_863_field.subfields.append(Subfield(code='j', value=f' {my_issue_month} '))
                    new_record.add_ordered_field(new_863_field)

                    # formatted_text.append(
                    #     f"=863 {41 if uncompressed else 40}$8 1.{c} $a {year} $b {volume} $c {issue_number} $d {issue_month}")
                    # print(formatted_text)
                    print(f'866 $a:             {my_866_subfield}')
                    print('new_863_field:     ', new_863_field)
                    c += 1

        elif '863' in new_record and '866' in new_record:
            print(f'Record No. {i} contains 863 fields')
        elif '866' not in new_record:
            print(f'Record No. {i} contains no 866 fields')
        else:
            print('Check Holdings Error')

        if '853' not in new_record and '866' in new_record:
            # noinspection PyTypeChecker
            new_853_field = Field(
                tag='853',
                indicators=['20', '$8 1 '],
            )
            if contain_volume:
                new_853_field.subfields.append(Subfield(code='a', value=' v. '))
            if contain_issue_number:
                new_853_field.subfields.append(Subfield(code='b', value=' no. '))
            new_853_field.subfields.append(Subfield(code='i', value=' (year) '))
            if contain_issue_month:
                if contain_issue_season:
                    new_853_field.subfields.append(Subfield(code='j', value=' (season) '))
                else:
                    new_853_field.subfields.append(Subfield(code='j', value=' (month) '))
            new_record.add_ordered_field(new_853_field)
            print('new_853_field:     ', new_853_field)
        if '007' not in new_record:
            new_record.add_ordered_field(new_007_field)
        records.append(new_record)

    print(f"\nExiting parse_file(marc_reader_object) method")
    print(f'\nNumber of records processed: {len(records)}\n')
    return records

def _collection_id_from_path(file_name):
    """Derive collection_id from file path (stem of filename)."""
    if not file_name:
        return None
    return Path(file_name).stem


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MARC holdings to 853/863 converter")
    parser.add_argument("--analyze", action="store_true", help="Analyze collection and generate/save regex pattern (with approval) before processing")
    parser.add_argument("--file", type=str, help="MARC file path (optional; if omitted, file dialog is used)")
    args = parser.parse_args()

    try:
        print("In the main function")
        file_name = args.file
        if not file_name:
            file_name = askopenfilename()
        if not file_name:
            print("No file selected.")
        else:
            # file_name = 'Dental_djb_holdings_extracted by 852$c_djb.mrc'
            print(f"Following file chosen: {file_name}")
            collection_id = _collection_id_from_path(file_name)
            if getattr(args, "analyze", False):
                analyze_collection_pattern(file_name, collection_id)
            my_records = read_file(file_name, collection_id=collection_id)
            if my_records:
                create_new_file(my_records, file_name.replace(".mrc", ".txt").replace(".mrk", ".txt"))
    except Exception as e:
        raise e
    finally:
        print("Exiting the main method.")
