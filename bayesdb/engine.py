#
#   Copyright (c) 2010-2014, MIT Probabilistic Computing Project
#
#   Lead Developers: Jay Baxter and Dan Lovell
#   Authors: Jay Baxter, Dan Lovell, Baxter Eaves, Vikash Mansinghka
#   Research Leads: Vikash Mansinghka, Patrick Shafto
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#

import time
import inspect
import os
import json
import datetime
import re
import operator
import copy
import math
import ast
import sys
import random
import threading
import multiprocessing
import Queue
import pandas

import pylab
import numpy
import matplotlib.cm
from collections import defaultdict

import bayesdb.settings as S
from persistence_layer import PersistenceLayer
import api_utils
import data_utils
import utils
import pairwise
import functions as funcs
import select_utils
import estimate_columns_utils
import plotting_utils
import parser as p

class Engine(object):
  def __init__(self, crosscat_host=None, crosscat_port=8007, crosscat_engine_type='local', seed=None, **kwargs):
    """ One optional argument that you may find yourself using frequently is seed.
    It defaults to random seed, but for testing/reproduceability purposes you may
    want a deterministic one. """
    
    self.persistence_layer = PersistenceLayer()
    self.parser = p.Parser()
    self.analyze_threads = dict() # Maps tablenames to whether they are doing an ANALYZE.

    if crosscat_host is None or crosscat_host == 'localhost':
      self.online = False
      
      # Only dependent on CrossCat when you actually instantiate Engine
      # (i.e., allow engine to be imported in order to examine the API, without CrossCat)
      from crosscat.CrossCatClient import get_CrossCatClient
      self.backend = get_CrossCatClient(crosscat_engine_type, seed=seed, **kwargs)
    else:
      self.online = True
      self.hostname = crosscat_host
      self.port = crosscat_port
      self.URI = 'http://' + self.hostname + ':%d' % self.port

  def call_backend(self, method_name, args_dict):
    """
    Helper function used to call the CrossCat backend, whether it is remote or local.
    Accept method name and arguments for that method as input.
    """
    if self.online:
      out, id = api_utils.call(method_name, args_dict, self.URI)
    else:
      method = getattr(self.backend, method_name)
      out = method(**args_dict)
    return out
  
  def help(self, method=None):
    help_methods = dict()
    help_methods['create'] = """
    CREATE BTABLE <btable> FROM <filename.csv>
    """

    help_methods['select']="""
    SELECT <columns|functions> FROM <btable> [WHERE <whereclause>] [ORDER BY <columns|functions>] [LIMIT <limit>]
    """
    help_methods['infer'] = """
    INFER <columns|functions> FROM <btable> [WHERE <whereclause>] [WITH CONFIDENCE <confidence>] [WITH <numsamples> SAMPLES] [ORDER BY <columns|functions>] [LIMIT <limit>]
    """
    help_methods['simulate'] = """
    SIMULATE <columns> FROM <btable> [WHERE <whereclause>] [GIVEN <column>=<value>] TIMES <times> [SAVE TO <file>]
    """
    help_methods['estimate'] = """
    ESTIMATE COLUMNS FROM <btable> [WHERE <whereclause>] [ORDER BY <functions>] [LIMIT <limit>] [AS <column_list>] 
    
    ESTIMATE PAIRWISE <function> FROM <btable> [FOR <columns>] [SAVE TO <file>] [SAVE CONNECTED COMPONENTS WITH THRESHOLD <threshold> AS <column_list>] 
    
    ESTIMATE PAIRWISE ROW SIMILARITY [WITH RESPECT TO <columns|column_lists>]FROM <btable> [FOR <rows>] [SAVE TO <file>] [SAVE CONNECTED COMPONENTS WITH THRESHOLD <threshold> [INTO|AS] <btable>]
    """

    help_methods['execute'] = """
    EXECUTE FILE <filename.bql>
    """
    
    help_methods['update'] = """
    UPDATE SCHEMA FOR <btable> SET <col1>=<type1>[,<col2>=<type2>...]
    """

    help_methods['initialize'] = """
    INITIALIZE <num_models> MODELS FOR <btable> [WITH CONFIG <config>]
    """

    help_methods['analyze'] = """
    ANALYZE <btable> [MODEL[S] <model_index>-<model_index>] FOR (<num_iterations> ITERATIONS | <seconds> SECONDS)
    """

    help_methods['list'] = """
    LIST BTABLES
    """

    help_methods['show'] = """
    SHOW SCHEMA FOR <btable>

    SHOW MODELS FOR <btable>

    SHOW DIAGNOSTICS FOR <btable>

    SHOW COLUMN LISTS FOR <btable>

    SHOW COLUMNS FOR <column_list|btable>

    SHOW ROW LISTS FOR <table>

    SHOW METADATA FOR <btable> [<metadata-key1> [, <metadata-key2>...]]

    SHOW LABEL FOR <btable> [<column-name-1> [, <column-name-2>...]]
    """

    help_methods['load'] = """
    LOAD MODELS <filename.pkl.gz> INTO <btable>
    """

    help_methods['save'] = """
    SAVE MODELS FROM <btable> TO <filename.pkl.gz>
    """

    help_methods['drop'] = """
    DROP BTABLE <btable>

    DROP MODEL[S] [<model_index>-<model_index>] FROM <btable>
    """

    help_methods['summarize'] = """
    SUMMARIZE <SELECT|INFER|SIMULATE>
    """

    help_methods['plot'] = """
    PLOT <SELECT|INFER|SIMULATE>
    """

    ## Important to make sure that http://probcomp.csail.mit.edu/bayesdb/docs/bql.html is up to date
    help_all = """
    Welcome to BQL help. 
    
    For the BQL documentation, please visit:
    http://probcomp.csail.mit.edu/bayesdb/docs/bql.html
    
    Here is a list of BQL commands and their syntax:
    """
    help_basic = """
    Welcome to BQL help. 
  
    If you know the query you wish to use, type 'HELP <query_name>'

    For all queries, type 'HELP ALL'
    
    For the complete BQL documentation, please visit: 
    http://probcomp.csail.mit.edu/bayesdb/docs/bql.html
    """
    
    if method == None:
      help_string = help_basic
    elif method == 'all':
      help_string = help_all + ''.join(help_methods.values())
    elif method in help_methods:
      help_string = help_all + help_methods[method]
    else:
      help_string = help_basic + '''
    The method you typed was not recognize. Try "HELP", "HELP ALL",
    or one of the following: 
    \tHELP ''' + '\n\tHELP '.join(help_methods.keys()).upper()

    return dict(message=help_string)

  def is_analyzing(self, tablename):
    return (tablename in self.analyze_threads and self.analyze_threads[tablename].isAlive())

  def drop_btable(self, tablename):
    """Delete table by tablename."""
    if self.is_analyzing(tablename):
      self.cancel_analyze(tablename)
      raise utils.BayesDBError('Error: cannot drop btable with ANALYZE in progress. Attempting to cancel ANALYZE; please retry drop btable once ANALYZE is successfully completed.')
    self.persistence_layer.drop_btable(tablename)
    return dict()

  def list_btables(self):
    """Return names of all btables."""
    return dict(columns=['btable'], data=[[name] for name in self.persistence_layer.list_btables()])

  def label_columns(self, tablename, mappings):
    """
    Add column labels to table in persistence layer, replacing
    labels without warning. Mappings is a dict of column names
    and their labels as given by the user.
    No length is enforced on labels - should we?
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)

    # Get column names for table so labels don't get written for nonexistent columns.
    M_c_full = self.persistence_layer.get_metadata_full(tablename)['M_c_full']
    colnames_full = utils.get_all_column_names_in_original_order(M_c_full)

    labels_edited = {}
    # Only add labels or overwrite one-by-one.
    for colname, label in mappings.items():
      if colname in colnames_full:
        self.persistence_layer.add_column_label(tablename, colname, label)
        labels_edited[colname] = label
      else:
        raise utils.BayesDBColumnDoesNotExistError(colname, tablename)

    labels = self.persistence_layer.get_column_labels(tablename)
    ret = {'data': [[c, l] for c, l in labels_edited.items()], 'columns': ['column', 'label']}
    ret['message'] = "Updated column labels for %s." % (tablename)
    return ret

  def show_labels(self, tablename, columnset):
    """
    Show column labels for the columns in columnstring
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)

    labels = self.persistence_layer.get_column_labels(tablename)

    # Get colnames from columnstring
    if columnset == None:
      colnames = labels.keys()
    else:
      column_lists = self.persistence_layer.get_column_lists(tablename)
      M_c = self.persistence_layer.get_metadata(tablename)['M_c']
      colnames = utils.process_column_list(columnset.asList(), M_c, column_lists, dedupe=True)
      colnames = [c.lower() for c in colnames]

    ret = {'data': [[c, l] for c, l in labels.items() if c in colnames], 'columns': ['column', 'label']}
    ret['message'] = "Showing labels for %s." % (tablename)
    return ret

  def update_metadata(self, tablename, mappings):
    """
    Add user metadata to table in persistence layer, replacing
    values without warning. Mappings is a dict of key names
    and their values as given by the user.
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)

    for key, value in mappings.items():
        self.persistence_layer.add_user_metadata(tablename, key, value)

    metadata = self.persistence_layer.get_user_metadata(tablename)
    ret = {'data': [[k, v] for k, v in metadata.items() if k in mappings.keys()], 'columns': ['key', 'value']}
    ret['message'] = "Updated user metadata for %s." % (tablename)
    return ret

  def show_metadata(self, tablename, keyset):
    """
    Get user metadata from persistence layer and show the values for the keys specified
    by the user. If no keystring is given, show all metadata key-value pairs.
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)

    metadata = self.persistence_layer.get_user_metadata(tablename)
    if keyset == None:
      metadata_keys = metadata.keys()
    else:
      metadata_keys = keyset.asList()

    ret = {'data': [[k, metadata[k]] for k in metadata_keys if k in metadata], 'columns': ['key', 'value']}
    ret['message'] = "Showing user metadata for %s." % (tablename)
    return ret

  def update_schema(self, tablename, mappings):
    """
    mappings is a dict of column name to 'continuous', 'multinomial',
    or 'ignore', or 'key'.
    Requires that models are already initialized.
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    if self.persistence_layer.has_models(tablename):
      raise utils.BayesDBError("Error: btable %s already has models. The schema may not be updated after models have been initialized; please either create a new btable or drop the models from this one." % tablename)
    
    msg = self.persistence_layer.update_schema(tablename, mappings)
    ret = self.show_schema(tablename)
    ret['message'] = 'Updated schema.'
    return ret
    
  def create_btable_from_existing(self, tablename, colnames_full, data, M_c):
    """
    Used in INTO statements to create a new btable as a portion of an existing one.
    """
    ## First, test if table with this name already exists, and fail if it does
    if self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBError('Btable with name %s already exists.' % tablename)

    # Remove row_id from table
    if 'row_id' in colnames_full:
      df = pandas.DataFrame(data=data, columns=colnames_full)
      utils.df_drop(df, ['row_id'], axis=1)
      data = df.to_records(index=False)
      colnames_full = df.columns

    cctypes_full = [utils.get_cctype_from_M_c(M_c, col) for col in colnames_full]
    T_full, M_r_full, M_c_full, _ = data_utils.gen_T_and_metadata(colnames_full, data, cctypes=cctypes_full)

    # variables without "_full" don't include ignored columns.
    raw_T, cctypes, colnames = data_utils.remove_ignore_cols(data, cctypes_full, colnames_full)
    T, M_r, M_c, _ = data_utils.gen_T_and_metadata(colnames, raw_T, cctypes=cctypes)
    self.persistence_layer.create_btable(tablename, cctypes_full, T, M_r, M_c, T_full, M_r_full, M_c_full, data)

    return dict(columns=colnames_full, data=[cctypes_full], message='Created btable %s. Schema taken from original btable:' % tablename)

  def create_btable(self, tablename, header, raw_T_full, cctypes_full=None):
    """Uplooad a csv table to the predictive db.
    cctypes must be a dictionary mapping column names
    to either 'ignore', 'continuous', or 'multinomial'. Not every
    column name must be present in the dictionary: default is continuous."""
    
    ## First, test if table with this name already exists, and fail if it does
    if self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBError('Btable with name %s already exists.' % tablename)

    # variables with "_full" include ignored columns.
    colnames_full = [h.lower().strip() for h in header]
    raw_T_full = data_utils.convert_nans(raw_T_full)
    if cctypes_full is None:
      cctypes_full = data_utils.guess_column_types(raw_T_full)
    T_full, M_r_full, M_c_full, _ = data_utils.gen_T_and_metadata(colnames_full, raw_T_full, cctypes=cctypes_full)

    # variables without "_full" don't include ignored columns.
    raw_T, cctypes, colnames = data_utils.remove_ignore_cols(raw_T_full, cctypes_full, colnames_full)
    T, M_r, M_c, _ = data_utils.gen_T_and_metadata(colnames, raw_T, cctypes=cctypes)
      
    self.persistence_layer.create_btable(tablename, cctypes_full, T, M_r, M_c, T_full, M_r_full, M_c_full, raw_T_full)

    return dict(columns=colnames_full, data=[cctypes_full], message='Created btable %s. Inferred schema:' % tablename)

  def show_schema(self, tablename):
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    
    metadata_full = self.persistence_layer.get_metadata_full(tablename)
    colnames = utils.get_all_column_names_in_original_order(metadata_full['M_c_full'])
    cctypes_full = metadata_full['cctypes_full']
    return dict(columns=['column', 'datatype'], data=zip(colnames, cctypes_full))

  def save_models(self, tablename):    
    """Opposite of load models! Returns the models, including the contents, which
    the client then saves to disk (in a pickle file)."""
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    
    return self.persistence_layer.get_models(tablename)

  def load_models(self, tablename, models):
    """Load these models as if they are new models"""
    # Models are stored in the format: dict[model_id] = dict[X_L, X_D, iterations].
    # We just want to pass the values.

    # For backwards compatibility with v0.1, where models are stored in the format:
    # dict[X_L_list, X_D_list, M_c, T]
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    
    if 'X_L_list' in models:
      print """WARNING! The models you are currently importing are stored in an old format
         (from version 0.1); it is deprecated and may not be supported in future releases.
         Please use "SAVE MODELS" to create an updated copy of your models."""
      
      old_models = models
      models = dict()
      for id, (X_L, X_D) in enumerate(zip(old_models['X_L_list'], old_models['X_D_list'])):
        models[id] = dict(X_L=X_L, X_D=X_D, iterations=0)
      
    result = self.persistence_layer.add_models(tablename, models.values())
    return self.show_models(tablename)

  def drop_models(self, tablename, model_indices=None):
    """Drop the specified models. If model_ids is None or all, drop all models."""
    if self.is_analyzing(tablename):
      raise utils.BayesDBError('Error: cannot drop models with ANALYZE in progress. Please retry once ANALYZE is successfully completed or canceled.')
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    
    self.persistence_layer.drop_models(tablename, model_indices)
    return self.show_models(tablename)
    
  def initialize_models(self, tablename, n_models=16, model_config=None):
    """
    Initialize n_models models.

    By default, model_config specifies to use the CrossCat model. You may pass 'naive bayes'
    or 'crp mixture' to use those specific models instead. Alternatively, you can pass a custom
    dictionary for model_config, as long as it contains a kernel_list, initializaiton, and
    row_initialization.
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    if self.is_analyzing(tablename):
      raise utils.BayesDBError('Error: cannot initialize models with ANALYZE in progress. Please retry once ANALYZE is successfully completed or canceled.')      

    # Get t, m_c, and m_r, and tableid
    M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)

    # Set model configuration parameters.
    if type(model_config) == str and model_config.lower() == 'naive bayes':
      model_config = dict(kernel_list=['column_hyperparameters'],
                          initialization='together',
                          row_initialization='together')
    elif type(model_config) == str and model_config.lower() == 'crp mixture':
      model_config = dict(kernel_list=['column_hyperparameters',
                                       'row_partition_hyperparameters',
                                       'row_partition_assignments'],
                          initialization='together',
                          row_initialization='from_the_prior')
    elif type(model_config) != dict or ('kernel_list' not in model_config) or ('initialization' not in model_config) or ('row_initialization' not in model_config):
      # default model_config: crosscat
      model_config = dict(kernel_list=(), # uses default
                          initialization='from_the_prior',
                          row_initialization='from_the_prior')
    else:
      raise utils.BayesDBError("Invalid model config")

    # Make sure the model config matches existing model config, if there are other models.
    existing_model_config = self.persistence_layer.get_model_config(tablename)
    if existing_model_config is not None and existing_model_config != model_config:
      raise utils.BayesDBError("Error: model config must match existing model config: %s" % str(existing_model_config))
      
    # Call initialize on backend
    X_L_list, X_D_list = self.call_backend('initialize',
                                           dict(M_c=M_c, M_r=M_r, T=T, n_chains=n_models,
                                                initialization=model_config['initialization'],
                                                row_initialization=model_config['row_initialization']))

    # If n_models is 1, initialize returns X_L and X_D instead of X_L_list and X_D_list
    if n_models == 1:
      X_L_list = [X_L_list]
      X_D_list = [X_D_list]
    
    model_list = list()    
    for X_L, X_D in zip(X_L_list, X_D_list):
      model_list.append(dict(X_L=X_L, X_D=X_D, iterations=0,
                             column_crp_alpha=[], logscore=[], num_views=[],
                             model_config=model_config))

    # Insert results into persistence layer
    self.persistence_layer.add_models(tablename, model_list)
    return self.show_models(tablename)

  def show_models(self, tablename):
    """Return the current models and their iteration counts."""
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)

    models = self.persistence_layer.get_models(tablename)
    modelid_iteration_info = list()
    for modelid, model in sorted(models.items(), key=lambda t:t[0]):
      modelid_iteration_info.append((modelid, model['iterations']))
    if len(models) == 0:
      raise utils.BayesDBNoModelsError(tablename)
    else:
      return dict(models=modelid_iteration_info)

  def show_diagnostics(self, tablename):
    """
    Display diagnostic information for all your models.
    TODO: generate plots of num_views, column_crp_alpha, logscore, and f_z stuff
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    
    models = self.persistence_layer.get_models(tablename)    
    data = list()
    for modelid, model in sorted(models.items(), key=lambda t:t[0]):
      if 'model_config' not in model:
        raise utils.BayesDBError("The models in %s were created with an old version of BayesDB which does not support model diagnostics." % tablename)
      data.append((modelid, model['iterations'], str(model['model_config'])))
    if len(models) == 0:
      raise utils.BayesDBError("No models for btable %s. Create some with the INITIALIZE MODELS command." % tablename)
    else:
      return dict(columns=['model_id', 'iterations', 'model_config'], data=data)

  def analyze(self, tablename, model_indices=None, iterations=None, seconds=None, ct_kernel=0, background=True):
    """
    Run analyze for the selected table. model_indices may be 'all' or None to indicate all models.

    Runs for a maximum of iterations, or a maximum number of seconds.
    
    Previously: this command ran in the same thread as this engine.
    Now: runs each model in its own thread, and does 10 seconds of inference at a time,
    by default. Each thread also has its own crosscat engine instance!
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    if not self.persistence_layer.has_models(tablename):
      raise utils.BayesDBNoModelsError(tablename)
    
    M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)
    
    max_model_id = self.persistence_layer.get_max_model_id(tablename)
    if max_model_id == -1:
      raise utils.BayesDBError("You must INITIALIZE MODELS before using ANALYZE.")
    models = self.persistence_layer.get_models(tablename)

    if model_indices is None or (str(model_indices).upper() == 'ALL'):
      modelids = sorted(models.keys())
    else:
      assert type(model_indices) == list
      modelids = model_indices

    first_model = models[modelids[0]]
    if 'model_config' in first_model and 'kernel_list' in first_model['model_config']:
      kernel_list = first_model['model_config']['kernel_list']
    else:
      kernel_list = () # default kernel list


    if not background:
      am = AnalyzeMaster(None)
      am.analyze_master(tablename, modelids, kernel_list, iterations, seconds, M_c, T, models, background, self)
      return dict(message="Analyze complete.")
    else:
      # Start analyze thread.
      if tablename in self.analyze_threads and self.analyze_threads[tablename].isAlive():
        raise utils.BayesDBError("%s is already being analzyed. Try using 'SHOW ANALYZE FOR %s' to get more information, or 'CANCEL ANALYZE FOR %s' to cancel the ANALYZE.")
      t = AnalyzeMaster(args=(tablename, modelids, kernel_list, iterations,
                              seconds, M_c, T, models, background, self))
      self.analyze_threads[tablename] = t
      t.start()
      # TODO: how to remove when done?
      return dict(message="Analyzing %s: models will be updated in the background." % tablename)

  def show_analyze(self, tablename):
    if tablename in self.analyze_threads and self.analyze_threads[tablename].isAlive():
      info = self.analyze_threads[tablename].get_info()
      return dict(message=info)
    else:
      return dict(message="Table %s is not currently being analyzed." % tablename)

  def cancel_analyze(self, tablename):
    if tablename in self.analyze_threads and self.analyze_threads[tablename].isAlive():
      self.analyze_threads[tablename].stop()
      return dict(message="Analyze for table %s is being canceled. Please wait a few moments for it to exit safely." % tablename)
    else:
      raise utils.BayesDBError("Table %s is not being analyzed." % tablename)              


  def infer(self, tablename, functions, confidence, whereclause, limit, numsamples, order_by=False, plot=False, modelids=None, summarize=False, hist=False, freq=False, newtablename=None):
    """
    Impute missing values.
    Sample INFER: INFER columnstring FROM tablename WHERE whereclause WITH confidence LIMIT limit;
    Sample INFER INTO: INFER columnstring FROM tablename WHERE whereclause WITH confidence INTO newtablename LIMIT limit;
    Argument newtablename == null/emptystring if we don't want to do INTO

    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    if not self.persistence_layer.has_models(tablename):
      raise utils.BayesDBNoModelsError(tablename)      
    
    if numsamples is None:
      numsamples=50 ##TODO: put this in a config file
      
    return self.select(tablename, functions, whereclause, limit, order_by,
                       impute_confidence=confidence, numsamples=numsamples, plot=plot, modelids=modelids, summarize=summarize, hist=hist, freq=freq, newtablename=newtablename)
    
  def select(self, tablename, functions, whereclause, limit, order_by, impute_confidence=None, numsamples=None, plot=False, modelids=None, summarize=False, hist=False, freq=False, newtablename=None):
    """
    BQL's version of the SQL SELECT query.
    
    First, reads codes from T and converts them to values.
    Then, filters the values based on the where clause.
    Then, fills in all imputed values, if applicable.
    Then, orders by the given order_by functions.
    Then, computes the queried values requested by the column string.

    query_colnames is the list of the raw columns/functions from the columnstring, with row_id prepended
    queries is a list of (query_function, query_args, aggregate) tuples, where 'query_function' is
      a function like row_id, column, similarity, or typicality, and 'query_args' are the function-specific
      arguments that that function takes (in addition to the normal arguments, like M_c, X_L_list, etc).
      aggregate specifies whether that individual function is aggregate or not

    queries: [(func, f_args, aggregate)]
    order_by: [(function, f_args, expr, val)]
    where_conditions: [(func, f_args, op, val)]
    
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)
    M_c_full, M_r_full, T_full = self.persistence_layer.get_metadata_and_table_full(tablename)

    X_L_list, X_D_list, M_c = self.persistence_layer.get_latent_states(tablename, modelids)
    column_lists = self.persistence_layer.get_column_lists(tablename)

    # Parse queries, where_conditions, and order by.
    queries, query_colnames = self.parser.parse_functions(functions, M_c, T, M_c_full, column_lists)
    if whereclause == None: 
      where_conditions = []
    else:
      where_conditions = self.parser.parse_where_clause(whereclause, M_c, T, M_c_full, column_lists)
      if len(where_conditions) > 0:
        assert len(where_conditions[0]) == 4
    if order_by == False:
      order_by = []
    else:
      order_by = self.parser.parse_order_by_clause(order_by, M_c, T, M_c_full, column_lists)

    # Fill in individual impute confs with impute_confidence if they're None and impute_confidence isn't:
    if impute_confidence is not None:
      for f_list in (where_conditions, queries, order_by):
        for i in range(len(f_list)):
          if f_list[i][0] == funcs._column and f_list[i][1][1] is None:
            new_f = list(f_list[i]) # (function, args, ...)
            new_f_args = list(new_f[1]) # args = (idx, conf)
            new_f_args[1] = impute_confidence
            new_f[1] = tuple(new_f_args)
            f_list[i] = tuple(new_f)

    if len(X_L_list) == 0:
      select_utils.check_if_functions_need_models(queries, tablename, order_by, where_conditions)

    ## Construct function list
    query_functions = [(q[0], q[1]) for q in queries]
    order_by_functions = [(o[0], o[1]) for o in order_by]
    where_functions = [(w[0], w[1]) for w in where_conditions]

    function_list = [q for q in query_functions]

    # Look up index in the function list, based on your own index.
    query_idxs = range(len(query_functions))
    order_by_idxs = []
    where_idxs = []

    query_size = len(query_functions)
    order_by_size = query_size
    
    def get_existing_f_idx(new_fun, function_list): 
        for f_idx, fun in enumerate(function_list):
          if new_fun == fun:
            return f_idx
        return False
        
    for o_idx, o_fun in enumerate(order_by_functions):
      existing_f_idx = get_existing_f_idx(o_fun, function_list)
      if existing_f_idx:
        order_by_idxs.append(existing_f_idx)
      else:
        function_list.append(o_fun)
        order_by_idxs.append(len(function_list) - 1)
        order_by_size += 1

    for w_idx, w_fun in enumerate(where_functions):
      existing_f_idx = get_existing_f_idx(w_fun, function_list)
      if existing_f_idx:
        where_idxs.append(existing_f_idx)
      else:
        function_list.append(w_fun)
        where_idxs.append(len(function_list) - 1)
          

    ## Now function list is constructed, so create the data table one row at a time,
    ## applying where clause in the process.
    ## data_tuples means row_id, row tuples
    data_tuples = []
    for row_id, T_row in enumerate(T):
      row = [None]*order_by_size
      row_values = select_utils.convert_row_from_codes_to_values(T_row, M_c) 
      where_clause_eval = select_utils.evaluate_where_on_row(row_id, row_values, where_conditions, M_c, M_c_full,
                               X_L_list, X_D_list, T, T_full, self, tablename, numsamples, impute_confidence)
      if type(where_clause_eval) == list:
        for w_idx, val in enumerate(where_clause_eval):
          # Now, store the already-computed values, if they're used for anything besides the where clause.
          if where_idxs[w_idx] < order_by_size:
            row[where_idxs[w_idx]] = val
        data_tuples.append((row_id, row))

          
    ## Now, order the rows. First compute all values that will be necessary for order by.
    if len(order_by) > 0:
      scored_data_tuples = list() # Entries are score, data_tuple      
      for (row_id, row) in data_tuples:
        scores = []
        for o_idx, (f, args, desc) in enumerate(order_by):
          # Look up whether function evaluated earlier in select. # TODO: can functions ever return None?
          if row[order_by_idxs[o_idx]] is not None:
            score = row[order_by_idxs[o_idx]]
          else:
            if f != funcs._column_ignore:
              score = f(args, row_id, row, M_c, X_L_list, X_D_list, T, self, numsamples)
            else:
              score = f(args, row_id, row, M_c_full, T_full, self)
            # Save value, if it will be displayed in output.
            if order_by_idxs[o_idx] < query_size:
              row[order_by_idxs[o_idx]] = score
            
          if desc:
            score *= -1
          scores.append(score)
        scored_data_tuples.append((tuple(scores), (row_id, row)))
      scored_data_tuples.sort(key=lambda tup: tup[0], reverse=False)
      data_tuples = [(tup[1][0], tup[1][1][:query_size]) for tup in scored_data_tuples]

    ## Now, apply the limit and compute the queried columns.
    
    # Compute aggregate functions just once, then cache them.
    aggregate_cache = dict()
    for q_idx, (query_function, query_args, aggregate) in enumerate(queries):
      if aggregate:
        aggregate_cache[q_idx] = query_function(query_args, None, None, M_c, X_L_list, X_D_list, T, self, numsamples)

    # Only return one row if all aggregate functions (row_id will never be aggregate, so subtract 1 and don't return it).
    if len(aggregate_cache) == len(queries):
      limit = 1

    # Iterate through data table, calling each query_function to fill in the output values.
    data = []
    row_count = 0
    for row_id, row in data_tuples:
      for q_idx, (query_function, query_args, aggregate) in enumerate(queries):
        if aggregate:
          row[q_idx] = aggregate_cache[q_idx]
        elif row[q_idx] is None:
          if query_function != funcs._column_ignore:
            row[q_idx] = query_function(query_args, row_id, row, M_c, X_L_list, X_D_list, T, self, numsamples)
          else:
            row[q_idx] = query_function(query_args, row_id, row, M_c_full, T_full, self)
      data.append(tuple(row))
      row_count += 1
      if row_count >= limit:
        break


    # Execute INTO statement
    if newtablename is not None:
      self.create_btable_from_existing(newtablename, query_colnames, data, M_c)

    ret = dict(data=data, columns=query_colnames)
    if plot:
      ret['M_c'] = M_c
    elif summarize | hist | freq:
      if summarize:
        data, columns = utils.summarize_table(ret['data'], ret['columns'], M_c)
      elif hist:
        data, columns = utils.histogram_table(ret['data'], ret['columns'], M_c)
      elif freq:
        data, columns = utils.freq_table(ret['data'], ret['columns'], M_c)
      ret['data'] = data
      ret['columns'] = columns
    return ret


  def simulate(self, tablename, functions, givens, numpredictions, order_by, plot=False, modelids=None, summarize=False, hist=False, freq=False, newtablename=None):
    """Simple predictive samples. Returns one row per prediction, with all the given and predicted variables."""
    # TODO: whereclause not implemented.
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    if not self.persistence_layer.has_models(tablename):
      raise utils.BayesDBNoModelsError(tablename)            

    X_L_list, X_D_list, M_c = self.persistence_layer.get_latent_states(tablename, modelids)
    if len(X_L_list) == 0:
      return {'message': 'You must INITIALIZE MODELS (and highly preferably ANALYZE) before using predictive queries.'}
    M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)

    numrows = len(M_r['idx_to_name'])
    name_to_idx = M_c['name_to_idx']

    ## TODO throw exception for <,> without dissallowing them in the values. 
    given_col_idxs_to_vals = dict()
    Y = None
    if givens is not None:
      Y = []
      for given_condition in givens.given_conditions:
        column_value = given_condition.value
        column_name = given_condition.column
        column_value = utils.string_to_column_type(column_value, column_name, M_c)
        given_col_idxs_to_vals[name_to_idx[column_name]] = column_value
        Y.append((numrows+1, name_to_idx[column_name], column_value))

      # map values to codes
      Y = [(r, c, data_utils.convert_value_to_code(M_c, c, colval)) for r,c,colval in Y]
    
    ## Parse queried columns.
    column_lists = self.persistence_layer.get_column_lists(tablename)
    queries, query_colnames = self.parser.parse_functions(functions, M_c, T, M_c_full=None, column_lists=column_lists)
    ##TODO check duplicates
    ##TODO check for no functions
    
    ##TODO col_indices, colnames are a hack from old parsing
    
    col_indices = [query[1][0] for query in queries[1:]]
    colnames = query_colnames[1:]
    query_col_indices = [idx for idx in col_indices if idx not in given_col_idxs_to_vals.keys()]
    Q = [(numrows+1, col_idx) for col_idx in query_col_indices]

    if len(Q) > 0:
      out = self.call_backend('simple_predictive_sample', dict(M_c=M_c, X_L=X_L_list, X_D=X_D_list, Y=Y, Q=Q, n=numpredictions))
    else:
      out = [[] for x in range(numpredictions)]
    assert type(out) == list and len(out) >= 1 and type(out[0]) == list and len(out) == numpredictions
    
    # convert to data, columns dict output format
    # map codes to original values
    data = []
    for out_row in out:
      row = []
      i = 0
      for idx in col_indices:
        if idx in given_col_idxs_to_vals:
          row.append(given_col_idxs_to_vals[idx])
        else:
          row.append(data_utils.convert_code_to_value(M_c, idx, out_row[i]))
          i += 1
      data.append(row)

    # Execute INTO statement
    if newtablename is not None:
      self.create_btable_from_existing(newtablename, colnames, data, M_c)
          
    ret = {'columns': colnames, 'data': data}
    if plot:
      ret['M_c'] = M_c
    elif summarize | hist | freq:
      if summarize:
        data, columns = utils.summarize_table(ret['data'], ret['columns'], M_c)
      elif hist:
        data, columns = utils.histogram_table(ret['data'], ret['columns'], M_c)
      elif freq:
        data, columns = utils.freq_table(ret['data'], ret['columns'], M_c)
      ret['data'] = data
      ret['columns'] = columns
    return ret

  def show_column_lists(self, tablename):
    """
    Return a list of all column list names.
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
      
    column_lists = self.persistence_layer.get_column_lists(tablename)
    return dict(columns=['column list'], data=[[k] for k in column_lists.keys()])

  def show_row_lists(self, tablename):
    """
    Return a list of all row lists, and their row counts.
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
      
    row_lists = self.persistence_layer.get_row_lists(tablename)
    return dict(row_lists=[(name, len(rows)) for (name, rows) in row_lists.items()])

    
  def show_columns(self, tablename, column_list=None):
    """
    Return the specified columnlist. If None, return all columns in original order.
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
      
    if column_list:
      column_names = self.persistence_layer.get_column_list(tablename, column_list)
    else:
      M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)      
      column_names = list(M_c['name_to_idx'].keys())
    return dict(columns=column_names)

  def show_model(self, tablename, modelid, filename):
    X_L_list, X_D_list, M_c = self.persistence_layer.get_latent_states(tablename)
    M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)
    import crosscat.utils.plot_utils
    crosscat.utils.plot_utils.plot_views(numpy.array(T), X_D_list[modelid], X_L_list[modelid], M_c, filename)

  def estimate_columns(self, tablename, functions, whereclause, limit, order_by, name=None, modelids=None, numsamples=None):

    """
    Return all the column names from the specified table as a list.
    First, columns are filtered based on whether they match the whereclause.
    The whereclause must consist of functions of a single column only.
    Next, the columns are ordered by other functions of a single column.
    Finally, the columns are limited to the specified number.

    ## allowed functions:
    # typicality(centrality)
    # dependence probability to <col>
    # mutual information with <col>
    # correlation with <col>
    """
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    
    X_L_list, X_D_list, M_c = self.persistence_layer.get_latent_states(tablename, modelids)
    M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)
    ##TODO deprecate functions in args
    column_indices = list(M_c['name_to_idx'].values())
    
    ## filter based on where clause
    where_conditions = self.parser.parse_column_whereclause(whereclause, M_c, T)
    if where_conditions is not None and len(X_L_list) == 0:
      raise utils.BayesDBNoModelsError(tablename)      
    column_indices = estimate_columns_utils.filter_column_indices(column_indices, where_conditions, M_c, T, X_L_list, X_D_list, self, numsamples)
    
    ## order
    if order_by and len(X_L_list) == 0:
      raise utils.BayesDBNoModelsError(tablename)      
    if order_by != False:
      order_by = self.parser.parse_column_order_by_clause(order_by, M_c)
      order_func_descriptions = [estimate_columns_utils.function_description(order_item, M_c) for order_item in order_by]
    else:
      order_func_descriptions = []

    # Get tuples of column estimation function values and column indices
    column_idx_tups = estimate_columns_utils.order_columns(column_indices, order_by, M_c, X_L_list, X_D_list, T, self, numsamples)
    
    # limit
    if limit != float('inf'):
      column_idx_tups = column_idx_tups[:limit]

    # convert indices to names and create data list of column names and associated function values
    column_names = []
    column_data = []

    for column_idx_tup in column_idx_tups:
      if type(column_idx_tup) == int:
        column_idx_tup = ((), column_idx_tup)
      column_name_temp = M_c['idx_to_name'][str(column_idx_tup[1])]
      column_names.append(column_name_temp)

      column_data_temp = [column_name_temp]
      column_data_temp.extend(column_idx_tup[0])

      column_data.append(column_data_temp)

    # save column list, if given a name to save as
    if name:
      self.persistence_layer.add_column_list(tablename, name, column_names)

    # Create column names ('column' followed by a string describing each function)
    ret = {'columns': ['column']}
    if column_data != []:
      ret['columns'].extend(order_func_descriptions)
      ret['data'] = column_data

    return ret

  def estimate_pairwise_row(self, tablename, function, row_list, clusters_name=None, threshold=None, modelids=None, numsamples=None):
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    X_L_list, X_D_list, M_c = self.persistence_layer.get_latent_states(tablename, modelids)
    M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)
    if len(X_L_list) == 0:
      raise utils.BayesDBNoModelsError(tablename)

    # TODO: deal with row_list
    if row_list:
      row_indices = self.persistence_layer.get_row_list(tablename, row_list)
      if len(row_indices) == 0:
        raise utils.BayesDBError("Error: Row list %s has no rows." % row_list)
    else:
      row_indices = None

    column_lists = self.persistence_layer.get_column_lists(tablename)
    
    # Do the heavy lifting: generate the matrix itself

    matrix, row_indices_reordered, clusters = pairwise.generate_pairwise_row_matrix(function, X_L_list, X_D_list, M_c, T, tablename, engine=self, row_indices=row_indices, cluster_threshold=threshold, column_lists=column_lists, numsamples=numsamples)
    title = 'Pairwise row %s for %s' % (function.function_id, tablename)      
    ret = dict(
      matrix=matrix,
      column_names=row_indices_reordered, # this is called column_names so that the plotting code displays them
      title=title,
      message = "Created " + title
      )

    # Create new btables from connected components (like into), if desired. Overwrites old ones with same name.
    if clusters is not None:
      cluster_name_tuples = []
      for i, cluster in enumerate(clusters):
        name = "%s_%d" % (clusters_name, i)
        num_rows = len(cluster)
        self.persistence_layer.add_row_list(tablename, name, cluster)
        cluster_name_tuples.append((name, num_rows))
      ret['clusters'] = clusters
      ret['row_lists'] = cluster_name_tuples

    return ret
    
  
  def estimate_pairwise(self, tablename, function_name, column_list=None, clusters_name=None, threshold=None, modelids=None, numsamples=None):
    if not self.persistence_layer.check_if_table_exists(tablename):
      raise utils.BayesDBInvalidBtableError(tablename)
    X_L_list, X_D_list, M_c = self.persistence_layer.get_latent_states(tablename, modelids)
    M_c, M_r, T = self.persistence_layer.get_metadata_and_table(tablename)
    if len(X_L_list) == 0:
      raise utils.BayesDBNoModelsError(tablename)

    if column_list:
      column_names = self.persistence_layer.get_column_list(tablename, column_list)
      if len(column_names) == 0:
        raise utils.BayesDBError("Error: Column list %s has no columns." % column_list)

      utils.check_for_duplicate_columns(column_names)
    else:
      column_names = None

    # Do the heavy lifting: generate the matrix itself
    matrix, column_names_reordered, clusters = pairwise.generate_pairwise_column_matrix(   \
        function_name, X_L_list, X_D_list, M_c, T, tablename, engine=self, column_names=column_names,
        cluster_threshold=threshold, numsamples=numsamples)
    
    title = 'Pairwise column %s for %s' % (function_name, tablename)      
    ret = dict(
      matrix=matrix,
      column_names=column_names_reordered,
      title=title,
      message = "Created " + title
      )

    # Add the column lists for connected clusters, if desired. Overwrites old ones with same name.
    if clusters is not None:
      cluster_name_tuples = []
      for i, cluster in enumerate(clusters):
        name = "%s_%d" % (clusters_name, i)
        column_names = [M_c['idx_to_name'][str(idx)] for idx in cluster]
        self.persistence_layer.add_column_list(tablename, name, column_names)
        cluster_name_tuples.append((name, column_names))
      ret['clusters'] = clusters
      ret['column_lists'] = cluster_name_tuples

    return ret

# helper functions
get_name = lambda x: getattr(x, '__name__')
get_Engine_attr = lambda x: getattr(Engine, x)
is_Engine_method_name = lambda x: inspect.ismethod(get_Engine_attr(x))

def get_method_names():
    return filter(is_Engine_method_name, dir(Engine))

def get_method_name_to_args():
    method_names = get_method_names()
    method_name_to_args = dict()
    for method_name in method_names:
        method = Engine.__dict__[method_name]
        arg_str_list = inspect.getargspec(method).args[1:]
        method_name_to_args[method_name] = arg_str_list
    return method_name_to_args


class StoppableThread(threading.Thread):
    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self, target, args):
        super(StoppableThread, self).__init__(target=target, args=args)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def stopped(self):
        return self._stop.isSet()    

class AnalyzeMaster(StoppableThread):
  def __init__(self, args):
    self.target = self.analyze_master    
    super(AnalyzeMaster, self).__init__(target=self.target, args=args)

  def get_info(self):
    elapsed_seconds = time.time() - self.start_time    
    if self.requested_iterations is None:
      # User specified time constraint
      remaining_seconds = self.total_seconds - elapsed_seconds
      if remaining_seconds > 0:
        return "Iterations completed: %d\nElapsed minutes: %0.2f\nRemaining minutes: %0.2f" % (self.iters_done, elapsed_seconds/60.0, remaining_seconds/60.0)
      else:
        return "Iterations completed: %d\nTime complete: waiting for final iteration to finish." % self.iters_done
    else:
      # User specified iterations constraint
      return "Iterations completed: %d\nRemaining iterations: %d\nElapsed minutes: %02.f\nEstimated remaining minutes:%0.2f" % (self.iters_done, self.requested_iterations - self.iters_done, elapsed_seconds/60.0, self.time_remaining_estimate/60.0)
    return timing_str
  
  def analyze_master(self, tablename, modelids, kernel_list, iterations, seconds, M_c, T, models, background, engine):
    """
    Helper function for analyze. This is the thread that runs in the background as the previous analyze
    command returns before analyze is complete.
    """

    """ New stuff: scrutinize carefully 
    analyze_args = dict(M_c=M_c, T=T, X_L=X_L_list, X_D=X_D_list, do_diagnostics=True,
                        kernel_list=kernel_list)
    if ct_kernel != 0:
      analyze_args['CT_KERNEL'] = ct_kernel
    
    analyze_args['n_steps'] = iterations
    if seconds is not None:
      analyze_args['max_time'] = seconds
    """
    self.iters_done = 0
    self.time_remaining_estimate = -1
    # Even if limited by seconds, still limit to 1000 iterations.
    if iterations is None:
      self.requested_iterations = None
      iterations = 1000
    else:
      self.requested_iterations = iterations

    # Tracking info
    self.start_time = time.time()
    self.total_seconds = seconds

    ## CrossCat analyze works by trying to do the specified number of iterations (which MUST be at least 1,
    ##   and it never does more than that number), and doesn't start a new iteration if the specified time
    ##   has passed (although stopped after X seconds appeared to be buggy).

    ## If we have only a number of iterations: we'll do that many iterations
    ## If we have only a number of seconds: we'll keep starting new transitions up until that many seconds
    ##   has elapsed.
    ## If we have both: we'll do the min of the two.

    ## We will call CrossCat in 1 iteration batches for now, and in a future commit, we'll support
    ## batches of larger size for smaller tables.
    # TODO: ignores iterations

    # TODO: kill final model if over time? not important bc just running in background.

    X_L_list = [models[i]['X_L'] for i in models.keys()]
    X_D_list = [models[i]['X_D'] for i in models.keys()]

    threads = []
    # Create a thread for each modelid. Each thread will execute one iteration of analyze.
    for i, modelid in enumerate(modelids):
      p = AnalyzeWorker(args=(modelid, tablename, kernel_list, iterations, seconds, M_c, T, X_L_list[i], X_D_list[i], background, engine))
      p.daemon = True
      p.start()
      threads.append(p)

    # before returning, make sure all threads have finished
    workers_done = False
    while not workers_done:
      time.sleep(.25)
      workers_done = True
      for p in threads:
        if not p.isAlive():
          p.join()
        else:
          workers_done = False

      # Get progress info
      info = zip(*[p.get_progress() for p in threads])
      iter_list = info[0]
      self.iters_done = min(iter_list)
      time_estimates = info[1]
      self.time_remaining_estimate = max(time_estimates)

      # If we are stopped, then stop all workers
      if self.stopped():
        for p in threads:
          if p.isAlive():
            p.stop()

    if background:
      print "\nAnalyze for table %s complete. Use 'SHOW MODELS FOR %s' to view models." % (tablename, tablename)

class AnalyzeWorker(StoppableThread):
  def __init__(self, args):
    self.target = self.do_analyze_for_model    
    super(AnalyzeWorker, self).__init__(target=self.target, args=args)
    self.iterations_done = 0
    self.time_per_model = 0

  def get_progress(self):
    time_left = (self.iterations - self.iterations_done)*self.time_per_model
    return self.iterations_done, time_left
    
  def do_analyze_for_model(self, modelid, tablename, kernel_list, iterations, seconds, M_c, T, X_L, X_D, background, engine):
      # TODO: this process should make a background thread for model writes!
      analyze_args = dict(M_c=M_c, T=T, do_diagnostics=True, kernel_list=kernel_list, \
                          X_L=X_L, X_D=X_D, n_steps=1)
      start_time = time.time()
      last_write_time = start_time
      models_per_call = 1

      self.total_analyze_time = 0
      self.total_write_time = 0
      
      self.iterations = iterations
      self.iterations_done = 0
      for i in range(iterations):
        cur_time = time.time()
        X_L, X_D, diagnostics_dict = engine.call_backend('analyze', analyze_args)
        self.total_analyze_time += time.time() - cur_time
        
        analyze_args['X_L'] = X_L
        analyze_args['X_D'] = X_D

        cur_time = time.time()
        elapsed = cur_time - start_time
        engine.persistence_layer.update_model(tablename, X_L, X_D, diagnostics_dict, modelid)
        self.total_write_time += time.time() - cur_time
        self.iterations_done += 1

        if self.stopped() or (elapsed >= seconds and seconds is not None):
          return

        # Update number of models per call, with a target time per call of 5 seconds.
        self.time_per_model = float(elapsed)/self.iterations_done
        if self.time_per_model > 5:
          models_per_call = 1
        else:
          models_per_call = int(5.0/self.time_per_model)
        if iterations - self.iterations_done < models_per_call:
          models_per_call = iterations - self.iterations_done
        analyze_args['n_steps'] = models_per_call
