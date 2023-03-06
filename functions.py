import requests
from pydash import py_
import pandas as pd
from jsonschema import validate

MDS_ENDPOINT = 'https://healdata.org/mds/metadata'
SLMD_SCHEMA = 'https://github.com/HEAL/heal-metadata-schemas/raw/133621a0d859a9c92c7f36eb47477cb1fa414468/study-level-metadata-schema/schema-clean/json/study-metadata-schema.json'

MAP = {'hdp_uid':'citation.heal_platform_persistent_ID',
       'project_title':'minimal_info.study_name',
       'study_description_summary':'minimal_info.study_description',
       'clinicaltrials_gov.OfficialTitle':'minimal_info.alternative_study_name',
       'clinicaltrials_gov.DetailedDescription':'minimal_info.alternative_study_description',
       'appl_id':'metadata_location.nih_application_id',
       'project_detail_url':'metadata_location.nih_reporter_link',
       'cedar_instance_id':'metadata_location.cedar_study_level_metadata_template_instance_ID',
       'clinical_trials_id':'metadata_location.clinical_trials_study_ID',
       'clinicaltrials_gov.CompletionDate':'data_availability.data_collection_finish_date',
       'clinicaltrials_gov.IPDSharingTimeFrame':'data_availability.data_release_start_date',
       'clinicaltrials_gov.StartDate':'data_availability.data_collection_start_date',
       'clinicaltrials_gov.OverallStatus':'data_availability.data_collection_status',
       'clinicaltrials_gov.EnrollmentCount':'data.subject_data_unit_of_collection_expected_number'}

# TODO: Modules citation, contacts and registrants, and findings not included
#       in CEDAR template
MISSING = {'citation': {'heal_funded_status':None,
                        'study_collection_status':None,
                        'study_collections':[],
                        'funding':[],
                        'investigators':[],
                        'heal_platform_citation':None},
           'findings': {'primary_publications':[],
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
    name = py_.get(obj, 'contact_pi_name').replace(',',' ').split()
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
        # Clean up values
        .update('study_type.study_stage',
                lambda v: [s.replace('Buisness Development', 'Business Development') for s in v])
        .update('data_availability.data_collection_status',
               lambda v: 'not started' if v=='Not yet recruiting' else v)
        .update('data_availability.data_collection_status',
               lambda v: 'started' if v=='Recruiting' else v)
        .update('data_availability.data_collection_status',
               lambda v: 'started' if v=='Enrolling by invitation' else v)
        .update('data_availability.data_collection_status',
               lambda v: 'started' if v=='Active, not recruiting' else v)
        .update('data_availability.data_collection_status',
               lambda v: 'finished' if v=='Completed' else v)
        .update('study_translational_focus.study_translational_focus',
               lambda v: 'Treatment of a Condition' if v=='Treatment' else v)
        .update('study_type.study_type_design',
               lambda v: [s.replace('–', '-') for s in v])
        .update('study_type.study_type_design',
               lambda v: [s.replace('Parallel Assignment','Randomized Control Trial') for s in v])
        .update('study_type.study_type_design',
               lambda v: [s.replace('Sequential Assignment','Randomized Control Trial') for s in v])
        .update('study_type.study_type_design',
               lambda v: [s.replace('Factorial Assignment','Randomized Control Trial') for s in v])
        .update('human_subject_applicability.sexual_identity_applicability',
               lambda v: [s.replace('Homosexual', 'Homosexual (gay/lesbian)') for s in v])
        .update('human_subject_applicability.sexual_identity_applicability',
               lambda v: [s.replace('Homosexual (gay/lesbian) (gay/lesbian)', 'Homosexual (gay/lesbian)') for s in v])
        .update('human_subject_applicability.sexual_identity_applicability',
               lambda v: [s.replace('Pansexual', 'Other') for s in v])
        .update('human_subject_applicability.sexual_identity_applicability',
               lambda v: [s.replace('Queer', 'Other') for s in v])
        .update('human_subject_applicability.sexual_identity_applicability',
               lambda v: [] if v==['Not applicable'] else v)
        .update('human_subject_applicability.gender_applicability',
               lambda v: [s.replace('Gender Queer', 'Other') for s in v])
        .update('human_subject_applicability.gender_applicability',
               lambda v: [s.replace('Agender, Non-binary, gender non-conforming', 'Other') for s in v])
        .update('human_subject_applicability.gender_applicability',
               lambda v: [s.replace('Trans Male', 'Female-to-male transsexual') for s in v])
        .update('human_subject_applicability.gender_applicability',
               lambda v: [s.replace('Trans Female', 'Male-to-female transsexual') for s in v])
        .update('human_subject_applicability.gender_applicability',
               lambda v: [] if v==['Not applicable'] else v)
        .update('human_subject_applicability.gender_applicability',
               lambda v: ['Intersexed' if s=='Intersex' else s for s in v])
        .update('human_subject_applicability.gender_applicability',
               lambda v: [s.replace('Cis Female', 'Other') for s in v])
        .update('human_subject_applicability.gender_applicability',
               lambda v: [s.replace('Cis Male', 'Other') for s in v])
        .update('metadata_location.nih_reporter_link',
               lambda v: v.replace('"','') if isinstance(v, str) else v)
        .update('data_availability.produce_data',
               lambda v: True if v=='Yes' else v)
        .update('data_availability.produce_data',
               lambda v: False if v=='No' else v)
        .update('data_availability.produce_other',
               lambda v: True if v=='Yes' else v)
        .update('data_availability.produce_other',
               lambda v: False if v=='No' else v)
        .update('human_subject_applicability.geographic_applicability',
               lambda v: [s.replace('US - Specific counties', 'US - Specific Counties') for s in v])
        .update('human_subject_applicability.geographic_applicability',
               lambda v: [s.replace('Non-US', 'Non US') for s in v])
        .update('human_subject_applicability.geographic_applicability',
               lambda v: [s.replace('US - Specific states', 'US - Specific States') for s in v])
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

def get_slmd(registered_only=True):
    """Fetch study level metadata"""
    guid_type = 'discovery_metadata' if registered_only else ''
    dfs = []
    for page in _get_records(guid_type):
        for study in page:
            slmd = page[study]['gen3_discovery']
            heal_slmd = _to_heal_slmd(slmd)
            df = pd.json_normalize(heal_slmd).rename(index={0:study})
            dfs.append(df)

    return pd.concat(dfs)

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
