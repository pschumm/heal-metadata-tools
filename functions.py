import requests
from pydash import py_
import pandas as pd
from jsonschema import validate

MDS_ENDPOINT = 'https://healdata.org/mds/metadata'
SLMD_SCHEMA = 'https://github.com/HEAL/heal-metadata-schemas/raw/133621a0d859a9c92c7f36eb47477cb1fa414468/study-level-metadata-schema/schema-clean/json/study-metadata-schema.json'

MAP = {'gen3_discovery.study_metadata.data':'data',
       'gen3_discovery.study_metadata.human_subject_applicability':'human_subject_applicability',
       'gen3_discovery.study_metadata.human_condition_applicability':'human_condition_applicability',
       'gen3_discovery.study_metadata.human_treatment_applicability':'human_treatment_applicability',
       'gen3_discovery.study_metadata.study_type':'study_type',
       'gen3_discovery.study_metadata.study_translational_focus':'study_translational_focus',
       'gen3_discovery.study_metadata.data_availability':'data_availability',
       'gen3_discovery.study_metadata.metadata_location':'metadata_location',
       'gen3_discovery.study_metadata.citation':'citation',
       '_hdp_uid':'citation.heal_platform_persistent_ID',
       'gen3_discovery.project_title':'minimal_info.study_name',
       'gen3_discovery.study_description_summary':'minimal_info.study_description',
       'clinicaltrials_gov.OfficialTitle':'minimal_info.alternative_study_name',
       'clinicaltrials_gov.DetailedDescription':'minimal_info.alternative_study_description'}

RECODES = {'study_type.study_stage':
               {'Buisness Development':'Business Development'},
           'data_availability.data_collection_status':
               {'Not yet recruiting':'not started',
                'Active, not recruiting':'started',
                'Enrolling by invitation':'started',
                'Recruiting':'started',
                'Completed':'finished'},
           'study_translational_focus.study_translational_focus':
               {'Treatment':'Treatment of a Condition'},
           'study_type.study_type_design':
               {'Parallel Assignment':'Randomized Control Trial',
                'Sequential Assignment':'Randomized Control Trial',
                'Factorial Assignment':'Randomized Control Trial'},
           'human_subject_applicability.sexual_identity_applicability':
               {'Homosexual':'Homosexual (gay/lesbian)',
                'Homosexual (gay/lesbian) (gay/lesbian)':'Homosexual (gay/lesbian)',
                'Pansexual':'Other',
                'Queer':'Other'},
           'human_subject_applicability.gender_applicability':
               {'Gender Queer':'Other',
                'Agender, Non-binary, gender non-conforming':'Other',
                'Trans Male':'Female-to-male transsexual',
                'Trans Female':'Male-to-female transsexual',
                'Intersex':'Intersexed',
                'Cis Female':'Female',
                'Cis Male':'Male'},
           'data_availability.produce_data':{'Yes':True, 'No':False},
           'data_availability.produce_other':{'Yes':True, 'No':False},
           'human_subject_applicability.geographic_applicability':
               {'US - Specific counties':'US - Specific Counties',
                'Non-US':'Non US',
                'US - Specific states':'US - Specific States'}
           }

# TODO: Shouldn't need this anymore
MISSING = {'findings': {'primary_publications':[],
                        'primary_study_findings':[],
                        'secondary_publications':[]}}

def _get_records(guid_type=None, data=True):
    """Fetch data records from HEAL MDS"""
    gtype = f'_guid_type={guid_type}&' if guid_type else ''
    offset = 0
    while True:
        query = f'{gtype}data={data}&offset={offset}&limit=2000'
        response = requests.get(f'{MDS_ENDPOINT}?{query}')
        results = response.json()
        if results:
            yield results
            offset += len(results)
        else:
            break

def _remap_items(obj):
    """Copy items from one path to another in obj based on MAP"""
    for k, v in MAP.items():
        parent = v.rsplit('.', 1)[0]
        if not py_.get(obj, v):
            if py_.get(obj, parent) in ['available','not_available','']:
                py_.unset(obj, parent)
            py_.set(obj, v, py_.get(obj, k))
    return obj

def _add_registrants(obj):
    """Add information on person registering study"""

    # TODO Complete once required information available in MDS
    py_.set(obj, 'contacts_and_registrants.registrants', [])
    return obj

def _add_repo_info(obj):
    """Add repository information to SLMD"""

    # TODO Handle additional information and potentially multiple repositories
    # TODO Fix error in required properties (i.e., 'repository_name, repository_study_ID')
    repo_name = py_.get(obj, 'repository')
    repo = {'repository_name':repo_name,
            'repository_name, repository_study_ID':''}
    py_.set(obj, 'metadata_location.data_repositories', [repo] if repo_name else [])
    return obj

def _add_contacts(obj):
    """Add information on contact person(s)"""

    # TODO Complete once required information available

    # N.B. Some records don't include info from NIH RePORTER
    try:
        name = py_.get(obj, 'nih_reporter.contact_pi_name').replace(',',' ').split()
    except AttributeError:
        name = ''
    if len(name) > 2:
        contact_info = [{'contact_first_name':name[1],
                         'contact_middle_initial':name[2],
                         'contact_last_name':name[0]}]
    elif len(name) == 2:
        contact_info = [{'contact_first_name':name[1],
                         'contact_last_name':name[0]}]
    else:
        contact_info = []

    py_.set(obj, 'contacts_and_registrants.contacts', contact_info)
    return obj

def _recode_items(obj):
    """Recode individual items based on RECODES"""

    def recode(rules, s, *args):
        for old, new in rules.items():
            if s==old:
                return new
        return s

    for path, rules in RECODES.items():
        sub_obj = py_.get(obj, path)
        new_obj = (py_.map_(sub_obj, py_.partial(recode, rules)) if
                   isinstance(sub_obj, list) else recode(rules, sub_obj))
        py_.set(obj, path, new_obj)

    return obj

def _remove_empties(dictionary):
    """Delete '' or None recursively from dictionary"""
    d = {}
    for k, v in dictionary.items():
        if isinstance(v, dict):
            v = _remove_empties(v)
        if (v not in ['', None]) or (k in ['study_description']):
            d[k] = v
    return d

def _to_heal_slmd(slmd, prune=False):
    """Translate SLMD from MDS to HEAL format

    Set prune = True to exclude additional properties.
    """
    heal_slmd = (py_(slmd)
        .map_keys(lambda v, k: py_.snake_case(k))
        # Fix byproduct of snake_case() above
        .rename_keys({'gen_3_discovery':'gen3_discovery'})
        .thru(_remap_items)
        .thru(_add_registrants)
        .thru(_add_repo_info)
        .thru(_add_contacts)
        .merge(MISSING)
        # Clean up types
        .update('study_type.study_stage', lambda v: v if v and isinstance(v, list)
                else ([v] if v else []))
        .update('data.subject_data_unit_of_collection_expected_number',
                lambda v: int(v) if v else None)
        .update('data.subject_data_unit_of_analysis_expected_number',
                lambda v: int(v) if v else None)
        .thru(_recode_items)
        # A few final miscellaneous cleanups
        .update('metadata_location.nih_reporter_link',
               lambda v: v.replace('"','') if v else None)
        .update('study_type.study_type_design',
               lambda v: [s.replace('–', '-') for s in v] if v else [])
        .update('human_subject_applicability.sexual_identity_applicability',
               lambda v: [] if v==['Not applicable'] else v)
        .update('human_subject_applicability.gender_applicability',
               lambda v: [] if v==['Not applicable'] else v)
        .update('metadata_location.nih_application_id',
               lambda v: str(v))
    ).value()

    if prune:
        heal_slmd = py_.pick(heal_slmd,
                             'minimal_info',
                             'metadata_location',
                             'citation',
                             'contacts_and_registrants',
                             'data_availability',
                             'findings',
                             'study_translational_focus',
                             'study_type',
                             'human_treatment_applicability',
                             'human_condition_applicability',
                             'human_subject_applicability',
                             'data')
        # TODO Would be better to handle this by revising the schema to include
        # null in type and enum for nullable fields
        heal_slmd = _remove_empties(heal_slmd)
        schema = requests.get(SLMD_SCHEMA).json()
        validate(instance=heal_slmd, schema=schema)

    return heal_slmd

def get_slmd():
    """Fetch study level metadata"""
    dfs = []
    for page in _get_records('discovery_metadata'):
        for study in page:
            slmd = page[study]
            heal_slmd = _to_heal_slmd(slmd)
            df = pd.json_normalize(heal_slmd).rename(index={0:study})
            dfs.append(df.dropna(axis=1))

    # Use copy to avoid "DataFrame is highly fragmented" warning
    return pd.concat(dfs).copy()

def _extract_fieldnames(schema):
    """Return fieldnames from JSON schema"""
    d = {}
    for k, v in schema.items():
        if k == 'properties':
            for p in schema[k]:
                d[p] = _extract_fieldnames(schema[k][p])
    return d if d else None

def get_heal_slmd_fields():
    """Return list of HEAL SLMD fields after normalization"""
    schema = requests.get(SLMD_SCHEMA).json()
    return (pd
        .json_normalize(_extract_fieldnames(schema))
        .columns
        .values
        .tolist()
    )
