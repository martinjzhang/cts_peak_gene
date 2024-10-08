import numpy as np
import pandas as pd 
import statsmodels.api as sm
import statsmodels.stats as stats
import scipy as sp
import scdrs
import math
import warnings
import random
from tqdm import tqdm
from Bio.SeqUtils import gc_fraction
import anndata as ad
import scanpy as sc
import ctar
import muon as mu
import sklearn as sk
from scipy.sparse import csc_matrix


def chunk_lists(lst, num_groups=20):
    """Returns dictionary {chunk #: all elements of lst that are in that chunk} all elements are kept in the same order
    
    Parameters:
    -----------
    lst: list or np.ndarray
        List to be split into chunks
    num_groups: int
        Number of chunks

    Returns
    -------
    list_chunks: dict
        Dictionary formated {chunk #: list of elements from lst in that chunk}. Order of lst is maintained when chunking.
    
    """
    group_size = len(lst)//num_groups

    g = 1
    list_chunks = {}

    while g<=num_groups+1:
        if g<num_groups+1:
            list_chunks[g] = lst[group_size*(g-1):group_size*g]
            
        else:
            list_chunks[g] = lst[group_size*(g-1):]
        g+=1

    return list_chunks


def pearson_corr_sparse(mat_X, mat_Y, var_filter=False):
    """Pairwise Pearson's correlation between columns in mat_X and mat_Y. Note that this will run
    much faster if given a csc_matrix rather than csr_matrix.

    Parameters
    ----------
    mat_X : np.ndarray
        First matrix of shape (N,M).
    mat_Y : np.ndarray
        Second matrix of shape (N,M).
        Assumes mat_X and mat_Y are aligned.
    var_filter : boolean
        Dictates whether to filter out columns with little to no variance.

    Returns
    -------
    mat_corr : np.ndarray
        Correlation array of shape (M,).
    no_var : np.ndarray
        A boolean mask where False represents excluded columns of mat_X and mat_Y of shape (N,M).
        
    """

    # Reshape
    if len(mat_X.shape) == 1:
        mat_X = mat_X.reshape([-1, 1])
    if len(mat_Y.shape) == 1:
        mat_Y = mat_Y.reshape([-1, 1])

    # Convert to sparse matrix if not already sparse
    if sp.sparse.issparse(mat_X) is False:
        mat_X = sp.sparse.csr_matrix(mat_X)
    if sp.sparse.issparse(mat_Y) is False:
        mat_Y = sp.sparse.csr_matrix(mat_Y)
    
    # Compute v_mean,v_var
    v_X_mean, v_X_var = scdrs.pp._get_mean_var(mat_X, axis=0)
    v_Y_mean, v_Y_var = scdrs.pp._get_mean_var(mat_Y, axis=0) 
    
    no_var = (v_X_var <= 1e-6) | (v_Y_var <= 1e-6)
    
    # This section removes columns with little to no variance.
    if var_filter and np.any(no_var):

        mat_X, mat_Y = mat_X[:,~no_var], mat_Y[:,~no_var]
        v_X_mean, v_X_var = v_X_mean[~no_var], v_X_var[~no_var]
        v_Y_mean, v_Y_var = v_Y_mean[~no_var], v_Y_var[~no_var]
        
    v_X_sd = np.sqrt(v_X_var.clip(1e-8))
    v_Y_sd = np.sqrt(v_Y_var.clip(1e-8))
    
    # Adjusted for column pairwise correlation only
    mat_corr = mat_X.multiply(mat_Y).mean(axis=0)
    mat_corr = mat_corr - v_X_mean * v_Y_mean
    mat_corr = mat_corr / v_X_sd / v_Y_sd

    mat_corr = np.array(mat_corr, dtype=np.float32)

    if (mat_X.shape[1] == 1) | (mat_Y.shape[1] == 1):
        return mat_corr.reshape([-1])
    if var_filter:
        return mat_corr, ~no_var

    return mat_corr


def initial_mcpval(del_cctrl,del_c):
    
    ''' Calculates two-tailed Monte Carlo p-value (only for B controls).
    
    Parameters
    ----------
    del_cctrl : np.ndarray
        Matrix of shape (gene#,n) where n is number of rand samples.
    del_c : np.ndarray
        Vector of shape (gene#,), which gets reshaped to (gene#,1).
    
    Returns
    ----------
    Vector of shape (gene#,) corresponding to the Monte Carlo p-value of each statistic.
    
    '''

    indicator = np.sum(np.abs(del_cctrl) >= np.abs(del_c.reshape(-1, 1)), axis=1)
    return (1+indicator)/(1+del_cctrl.shape[1])


def center_ctrls(ctrl_array,main_array):

    ''' Centers control and focal correlation arrays according to control mean and std.
    
    Parameters
    ----------
    ctrl_array : np.ndarray
        Array of shape (N,B) where N is number of genes and B is number
        of repetitions (typically 1000x). Contains correlation between
        focal gene and random peaks.
    main_array : np.ndarray
        Array of shape (N,) containing correlation between focal gene
        and focal peaks.
    
    Returns
    ----------
    ctrls : np.ndarray
        Array of shape (N*B,) containing centered correlations between
        focal gene and random peaks.
    main : np.ndarray
        Array of shape (N,) containing centered correlations between
        focal gene and focal peak, according to ctrl mean and std.
        
    '''
    
    
    # Takes all ctrls and centers at same time
    # then centers putative/main with same vals
    mean = np.mean(ctrl_array,axis=1)
    std = np.std(ctrl_array,axis=1)
    ctrls = (ctrl_array - mean.reshape(-1,1)) / std.reshape(-1,1)
    main = (main_array - mean) / std
    
    return ctrls.flatten(), main


def mc_pval(del_cctrl_full,del_c):

    ''' Calculates MC p-value using centered control and focal correlation arrays across
    all controls (N*B).
    
    Parameters
    ----------
    del_cctrl_full : np.ndarray
        Array of shape (N,B) where N is number of genes and B is number
        of repetitions (typically 1000x). Contains correlation/delta correlation
        between focal gene and random peaks.
    del_c : np.ndarray
        Array of shape (N,) containing correlation/delta correlation between
        focal gene and focal peaks.
    
    Returns
    ----------
    full_mcpvalue : np.ndarray
        Array of shape (N,) containing MC p-value corresponding to Nth peak-gene pair
        against all centered contrl correlation/delta correlation pairs.
        
    '''

    # Center first
    del_cctrl_full_centered,del_c_centered = center_ctrls(np.abs(del_cctrl_full),np.abs(del_c))
    del_cctrl_full_centered = np.sort(del_cctrl_full_centered)
    n,b = del_cctrl_full.shape
    
    # Search sort returns indices where element would be inserted
    indicator = len(del_cctrl_full_centered) - np.searchsorted(del_cctrl_full_centered,del_c_centered)
    return (1+indicator)/(1+(n*b))





#####################################################################################
######################################## WIP ########################################
#####################################################################################



def add_corr(mdata):

    '''Adds pearson corr for putative links to existing mdata.

    Parameters
    ----------
    mdata : mu.MuData
        MuData object of shape (#cells,#peaks). Contains DataFrame under mdata.uns.peak_gene_pairs
        containing columns ['index_x','index_y'] that correspond to peak and gene indices in atac.X
        and rna.X respectively, as well as mdata.uns.control_peaks containing randomly generated peaks.
        If mdata.uns.control_peaks doesn't exist yet, will create it.
    
    Returns
    ----------
    mdata : mu.MuData
        Updates mdata input with mdata.uns.corr, an np.ndarray of shape (#peak-gene pairs).
    
    '''

    try:
        mdata.uns['peak_gene_pairs']
    except KeyError:
        print('Attempting to add peak-gene pairs.')
        find_peak_gene_pairs(mdata)

    # Extract peak-gene pairs and their respective atac and rna exp values
    peaks_df = mdata.uns['peak_gene_pairs']
    atac_Xs = mdata['atac'].X[:,peaks_df.index_x.values]
    rna_Xs = mdata['rna'].X[:,peaks_df.index_y.values]

    # Calculate corr
    corr = pearson_corr_sparse(atac_Xs,rna_Xs,var_filter=True)

    # Store in mdata.uns
    mdata.uns['peak_gene_corr'] = corr
    
    return mdata
    

def get_corrs(adata):

    '''Adds pearson corr for putative links to a links AnnData.

    Parameters
    ----------
    adata : ad.AnnData
        AnnData of shape (#cells, #peak-gene pairs) containing rna and atac layers.
        See build_adata.
    
    Returns
    ----------
    adata : ad.AnnData
        Updates given AnnData with correlation between peak-gene pairs of shape
        (#cells, #peak-gene pairs with variance > 1e-6).
    
    '''

    assert type(adata) == type(ad.AnnData()), 'Must be AnnData.'

    # Calculate corr
    corr = pearson_corr_sparse(adata.layers['rna'],adata.layers['atac'],var_filter=True)

    # Remove pairs that have fail var_filter from adata obj
    adata = adata[:,corr[1]].copy()
    adata.var['corr'] = corr[0].flatten()
    
    return adata


######################### Control corr #########################


def gc_content(adata,genome_file='/projects/zhanglab/users/ana/liftover/GRCh38.p13.genome.fa.bgz'):
    
    ''' Finds GC content for peaks.
    
    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of size (N,M) with atac mod containing peak range information
        and peak dataframe (in adata.uns). Also assumed to have 'gene_id' column in vars.
    genome_file : genome.fa.bgz file
        Reference genome in fasta file format.
    
    Returns
    ----------
    gc : np.ndarray
        Array of shape (N,) containing GC content of each peak.
        
    '''

    # Get sequences
    atac_seqs = mu.atac.tl.get_sequences(adata,None,fasta_file=genome_file)
    # Store each's gc content
    gc = np.empty(adata.shape[1])
    i=0
    for seq in atac_seqs:
        gc[i] = gc_fraction(seq)
        i+=1
    return gc


def get_bins(adata, num_bins=5, peak_col='gene_ids', gc=True):

    ''' Obtains GC and MFA bins for ATAC peaks.
    
    Parameters
    ----------
    adata : ad.AnnData
        AnnData
    num_bins : int
        Number of desired bins for MFA and GC groupings.
    peak_col : str
        Label for peak column.
    gc : bool
        If True, GC content will be taken.
    
    Returns
    ----------
    bins : pd.DataFrame
        DataFrame of length (N) with columns [peak_col,'index_x','mfa','gc','combined_mfa_gc']
    
    '''

    # start emtpy df
    bins = pd.DataFrame()
    # duplicated peaks will be in the same bin
    # to avoid this, filter them out first
    unique = ~adata.var.duplicated(subset='peak')

    # Obtain mfa and gc content
    bins['peak'] = adata.var[peak_col][unique]
    # accessing by row is faster
    adata.var['index_z'] = range(len(adata.var))
    bins['ind'] = adata.var.index_z[unique]
    # only take the unique peak info
    #atac_X = adata[:,unique].layers['atac_raw']
    atac_X = adata[:,unique].X
    bins['mfa'] = atac_X.mean(axis=0).A1
    print('MFA done.')

    
    if gc:
        bins['gc'] = gc_content(adata[:,unique])
        print('GC done.')
    # Put into bins
    bins['mfa_bin'] = pd.qcut(bins['mfa'].rank(method='first'), num_bins, labels=False, duplicates="drop")
    
    if gc:
        bins['gc_bin'] = pd.qcut(bins['gc'].rank(method='first'), 2, labels=False, duplicates="drop")
        # create combined bin if mfa+gc is included
        bins['combined_mfa_gc']=bins['mfa_bin']* 10 + bins['gc_bin']
    
    return bins


def create_ctrl_peaks(adata,num_bins=3,b=200,update=True,gc=True,peak_col='gene_ids'):

    ''' Obtains GC and MFA bins for ATAC peaks.
    
    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks). Contains DataFrame under mdata.uns.peak_gene_pairs
        containing columns ['index_x','index_y'] that correspond to peak and gene indices in atac.X
        and rna.X respectively.
    num_bins : int
        Number of desired bins for MFA and GC groupings EACH.
    b : int
        Number of desired random peaks per focal peak-gene pair.
    update : bool
    	If True, updates original AnnData with adata.varm.control_peaks.
    gc : bool
        If True, gets MFA and GC bins. Else, only gets MFA bins.
    peak_col : str
        Label for column containing peak IDs.
    
    Returns
    ----------
    ctrl_peaks : np.ndarray
        Matrix of length (#peaks,n) where n is number of random peaks generated (1000 by default).
    
    '''
    
    bins = get_bins(adata, num_bins=num_bins, gc=gc, peak_col=peak_col)
    print('Get_bins done.')
    
    # Group indices for rand_peaks
    if gc: 
        bins_grouped = bins[['ind','combined_mfa_gc']].groupby(['combined_mfa_gc']).ind.apply(np.array)
    else: bins_grouped = bins[['ind','mfa_bin']].groupby(['mfa_bin']).ind.apply(np.array)
    
    

    # Generate random peaks
    ctrl_peaks = np.empty((len(bins),b))
    # iterate through each row of bins
    for i,peak in enumerate(bins.itertuples()):
        if gc: row_bin = bins_grouped.loc[peak.combined_mfa_gc]
        else: row_bin = bins_grouped.loc[peak.mfa_bin]
        row_bin_copy = row_bin[row_bin!=peak.ind]
        ctrl_peaks[i] = np.random.choice(row_bin_copy, size=(b,), replace=False)
    
    # for duplicated peaks, simply copy these rows
    # since they will be compared with different genes anyway
    ind,_ = pd.factorize(adata.var[peak_col])
    ctrl_peaks = ctrl_peaks[ind,:].astype(int)

    if update:
        # Add adata.varm.control_peaks
        adata.varm['control_peaks'] = ctrl_peaks
    
    return ctrl_peaks


def control_corr(adata, b=200, update=True, ct=False, is_atac = False, rna_adata = None):

    '''Calculates pearson corr for controls.

    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks). Contains DataFrame under mdata.uns.peak_gene_pairs
        containing columns ['index_x','index_y'] that correspond to peak and gene indices in atac.X
        and rna.X respectively, as well as mdata.uns.control_peaks containing randomly generated peaks.
        If mdata.uns.control_peaks does not exist, will create it.
    b : int
        Number of desired random peaks per focal peak-gene pair. Should match
        mdata.uns['control_peaks'].shape[1].
    update : bool
        If True, updates original AnnData with adata.varm['control_corr']
    ct : bool
    	If True, identifies using original atac.X information, as control pairs are not limited to pairs
    	highly expressed within CT.
    is_atac : bool
        If True, adata is a an AnnData object of shape (#cells,#peaks) where adata.X is the raw atac data.
    rna_adata : ad.AnnData
        AnnData object of shape (#cells, #genes), where rna_adata.X contains gene normalized expression levels

    
    Returns
    ----------
    ctrl_corr : np.ndarray
        Array of shape (N,B).
    
    '''

    try:
        adata.varm['control_peaks']
    except KeyError:
        print('Adding control_peaks.')
        create_ctrl_peaks(adata,b=b)

    ctrl_peaks = adata.varm['control_peaks']

    if ct:
        atac_Xs = adata.uns['original_atac']
    else:
        if is_atac: 
            atac_Xs = adata.X
        else:
            atac_Xs = adata.layers['atac']
    if rna_adata is None:
        rna_Xs = adata.layers['rna']
    else: 
        rna_Xs = rna_adata.X
    
    ctrl_corr = np.empty([adata.shape[1], b])
    
    for row in tqdm(range(adata.shape[1])):
        ctrl_corr[row,:] = pearson_corr_sparse(atac_Xs[:,ctrl_peaks[row]],rna_Xs[:,[row]])

    if update:
        adata.varm['control_corr'] = ctrl_corr
    
    return ctrl_corr

def control_corr_new(adata, b=200, update=True, ct=False, is_atac = False, rna_adata = None):# chunk=0, chunk_size=1046):

    '''Calculates pearson corr for controls.

    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks). Contains DataFrame under mdata.uns.peak_gene_  pairs
        containing columns ['index_x','index_y'] that correspond to peak and gene indices in atac.X
        and rna.X respectively, as well as mdata.uns.control_peaks containing randomly generated peaks.
        If mdata.uns.control_peaks does not exist, will create it.
    b : int
        Number of desired random peaks per focal peak-gene pair. Should match
        mdata.uns['control_peaks'].shape[1].
    update : bool
        If True, updates original AnnData with adata.varm['control_corr']
    ct : bool
    	If True, identifies using original atac.X information, as control pairs are not limited to pairs
    	highly expressed within CT.
    is_atac : bool
        If True, adata is a an AnnData object of shape (#cells,#peaks) where adata.X is the raw atac data.
    rna_adata : ad.AnnData
        AnnData object of shape (#cells, #genes), where rna_adata.X contains gene normalized expression levels

    
    Returns
    ----------
    ctrl_corr : np.ndarray
        Array of shape (N,B).
    
    '''

    try:
        adata.varm['control_peaks']
    except KeyError:
        print('Adding control_peaks.')
        create_ctrl_peaks(adata,b=b)

    ctrl_peaks = adata.varm['control_peaks']

    if ct:
        atac_Xs = adata.uns['original_atac']
    else:
        if is_atac: 
            atac_Xs = adata.X
        else:
            atac_Xs = adata.layers['atac']

    

    ctrl_corr = np.empty([adata.shape[1], b])
   
    for row in tqdm(range(adata.shape[1])):
        print("In control_corr ",row)
        ctrl_corr[row,:] = pearson_corr_sparse(atac_Xs[:,ctrl_peaks[row]],rna_adata[:,[row]].X)

    if update:
        adata.varm['control_corr'] = ctrl_corr
    
    return ctrl_corr




def control_corr_fast(adata, b=200, update=True, ct=False, is_atac = False, rna_adata = None):# chunk=0, chunk_size=1046):

    '''Calculates pearson corr for controls. Runs correlation of multiple ctrl_peak-gene pairs at once instead of one at a time.

    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks). Contains DataFrame under mdata.uns.peak_gene_  pairs
        containing columns ['index_x','index_y'] that correspond to peak and gene indices in atac.X
        and rna.X respectively, as well as mdata.uns.control_peaks containing randomly generated peaks.
        If mdata.uns.control_peaks does not exist, will create it.
    b : int
        Number of desired random peaks per focal peak-gene pair. Should match
        mdata.uns['control_peaks'].shape[1].
    update : bool
        If True, updates original AnnData with adata.varm['control_corr']
    ct : bool
    	If True, identifies using original atac.X information, as control pairs are not limited to pairs
    	highly expressed within CT.
    is_atac : bool
        If True, adata is a an AnnData object of shape (#cells,#peaks) where adata.X is the raw atac data.
    rna_adata : ad.AnnData
        AnnData object of shape (#cells, #genes), where rna_adata.X contains gene normalized expression levels

    
    Returns
    ----------
    ctrl_corr : np.ndarray
        Array of shape (N,B).
    
    '''

    try:
        adata.varm['control_peaks']
    except KeyError:
        print('Adding control_peaks.')
        create_ctrl_peaks(adata,b=b)

    ctrl_peaks = adata.varm['control_peaks']

    if ct:
        atac_Xs = adata.uns['original_atac']
    else:
        if is_atac: 
            atac_Xs = adata.X
        else:
            atac_Xs = adata.layers['atac']
        
    chunked_ctrl_peaks = chunk_lists(ctrl_peaks.tolist(), 15)
    rna_col_chunks = chunk_lists(list(range(rna_adata.shape[1])),15)
    pearson_corrs = []
    for i in chunked_ctrl_peaks:
        index_arr = np.concatenate(chunked_ctrl_peaks[i],axis=0)
          
        rows, cols = rna_adata[:, rna_col_chunks[i]].X.shape    
        
        ctrl_corr_chunk = pearson_corr_sparse(csc_matrix(atac_Xs.toarray()[:, index_arr.tolist()]),csc_matrix(np.tile(rna_adata[:, rna_col_chunks[i]].X.toarray()[:, :, np.newaxis], (1, 1, b)).reshape(rows, cols * b)))
        
        pearson_corrs.append(ctrl_corr_chunk.reshape(cols,b))

    ctrl_corr = np.vstack(pearson_corrs)
    if update:
        adata.varm['control_corr'] = ctrl_corr
    
    return ctrl_corr



    
def shuffle_columns_all_at_once(atac_chunk_matrix_dense, num_shuffles=200):
    """ Randomly shuffles the columns of matrix num_shuffles times and returns a 3D array with all shuffled matrices.

    Parameters
    ----------
    atac_chunk_matrix_dense : np.array
        Array of shape (m,n)

    num_shuffles : int
        Number of shuffled matrices to make.

    Returns
    ----------
    
    shuffled_matrix : np.array
        Array of shape (m, n, num_shuffles)
    
    """
    
    
    m, n = atac_chunk_matrix_dense.shape
    # Create an array to store all permutations
    #shuffled_matrix = np.empty((num_shuffles, m, n))

    col_list = []
    # Generate permutations for each column
    for i in range(n):
        column = atac_chunk_matrix_dense[:, i]
        if (column.shape[1] ==1):
            column = np.squeeze(column, axis=1)
        # Create a matrix where each row is the original column
        repeated_column = np.tile(column, (num_shuffles, 1)).T
        
        # Shuffle the columns of this matrix
        shuffled_column_matrix = np.squeeze(np.apply_along_axis(np.random.permutation, 0, repeated_column), axis=0)
        
        # Assign the shuffled columns to the output matrix
        col_list.append(np.expand_dims(shuffled_column_matrix.T, axis = 2))
        #shuffled_matrix[:, :, i] = shuffled_column_matrix.T
    shuffled_matrix = np.concatenate(col_list, axis = 2)

    return shuffled_matrix
    
def control_corr_permutation_chunked(adata, num_ctrls=100, rna_adata = None, num_groups=19):

    """Calculates pearson corr for controls.

    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks). Contains DataFrame under mdata.uns.peak_gene_  pairs
        containing columns ['index_x','index_y'] that correspond to peak and gene indices in atac.X
        and rna.X respectively, as well as mdata.uns.control_peaks containing randomly generated peaks.
        If mdata.uns.control_peaks does not exist, will create it.

    num_ctrls : int
        Number of permuted control matrices to make.

    rna_adata : ad.AnnData
        AnnData object of shape (#cells, #genes), where rna_adata.X contains gene normalized expression levels.

    num_groups : int
        Number of chunks in which to process the columns of rna_adata.

    Returns
    ----------
    
    control_corr : np.ndarray
        Array of shape (num_ctrls, #peaks)
    
    """

    group_size = rna_adata.shape[1]//num_groups
    control_corr_list = []
    g = 1
    while g <= num_groups + 1:
        if g<num_groups+1:
            rna_chunk_idx = rna_adata.var.index.tolist()[group_size*(g-1):group_size*g]
            rna_chunk_matrix_dense = rna_adata.X[:,group_size*(g-1):group_size*g].todense()
            atac_chunk_matrix_dense = adata.X[:,group_size*(g-1):group_size*g].todense()

        else:
            rna_chunk_idx = rna_adata.var.index.tolist()[group_size*(g-1):]
            rna_chunk_matrix_dense = rna_adata.X[:,group_size*(g-1):].todense()
            atac_chunk_matrix_dense = adata.X[:,group_size*(g-1):].todense()
            
        rna_chunk_matrix = csc_matrix(rna_chunk_matrix_dense.astype(np.float32))
        
        num_columns = atac_chunk_matrix_dense.shape[1]
        ctrl_corr_chunk_list = []
        atac_chunk_tensor = shuffle_columns_all_at_once(atac_chunk_matrix_dense, num_ctrls)

        ctrl_corr_samples=[]
        for j in range(num_ctrls):
            ctrl_corr_chunk = ctar.method.pearson_corr_sparse(rna_chunk_matrix,csc_matrix(atac_chunk_tensor[j].astype(np.float32)))
            ctrl_corr_samples.append(ctrl_corr_chunk)
            
        ctrl_corr_chunk = np.stack(ctrl_corr_samples, axis=0)
        ctrl_corr_chunk =np.squeeze(ctrl_corr_chunk,axis=1)
        
        if (len(ctrl_corr_chunk.shape) == 1) & (ctrl_corr_chunk.shape[0] == 1):
            ctrl_corr_chunk = ctrl_corr_chunk[None,:]

        #ctrl_corr_chunk_list.append(ctrl_corr_chunk)
            
        # Stack all shuffled correlations
        #ctrl_corr_chunk_array = np.stack(ctrl_corr_chunk_list, axis=1)
        control_corr_list.append(ctrl_corr_chunk)
        g += 1

    control_corr = np.concatenate(control_corr_list, axis=1)
    return control_corr


def get_pvals(adata, control_metric='control_corr', metric='corr', alpha=0.05):

    ''' Adds mc_pval and mc_qval to AnnData.

    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks). Should contain metric under adata.var,
        and control_metric under adata.varm.
    control_metric : str
        Column name in adata.varm (pd.DataFrame of length #peaks) pertaining to 
        control metric, e.g. 'control_corr' or 'delta_control_corr'.
    metric : str
        Column name in adata.varm (pd.DataFrame of length #peaks) pertaining to 
        putative metric, e.g. 'corr' or 'delta_corr'.
    alpha : int
        Alpha threshold for BH FDR correction.
    
    Returns
    ----------
    adata : ad.AnnData
        AnnData of shape (#cells,#peaks) updated with mc_pval and mc_qval.

    '''
    
    # Adds mc_pval to AnnData
    adata.var['mc_pval'] = mc_pval(adata.varm[control_metric], adata.var[metric].values)
    
    # Adds BH FDR qvals to AnnData
    adata.var['mc_qval'] = stats.multitest.multipletests(adata.var['mc_pval'].values, alpha=alpha, method='fdr_bh')[1]
    
    return adata



########################### CT-specific ###########################


def filter_lowexp(atac, rna, min_pct=0.05, min_mean=0):
    
    ''' Filter out cells with few expressing cells or lower absolute mean expression.

    Parameters
    ----------
    atac : sp.sparse_array
        Sparse array of shape (#cells,#peaks) containing raw ATAC data.
    atac : sp.sparse_array
        Sparse array of shape (#cells,#peaks) containing raw RNA data.
    min_pct : float
        Value between [0,1] of the minimum number of nonzero ATAC and RNA
        values for a given link.
    min_pct : float
        Minimum absolute mean for ATAC and RNA expression.
    
    Returns
    ----------
    lowexp_mask : np.array (dtype: bool)
        Boolean array of shape (#peaks) dictating which peaks were filtered out.

    '''

    mean_atac, mean_rna = np.abs(atac.mean(axis=0)), np.abs(rna.mean(axis=0))
    nnz_atac, nnz_rna = atac.getnnz(axis=0) / atac.shape[0], rna.getnnz(axis=0) / atac.shape[0]
    lowexp_mask = (((nnz_atac > min_pct) & (nnz_rna > min_pct)) & ((mean_atac > min_mean) & (mean_rna > min_mean))).A1
    
    return lowexp_mask



def filter_vars(adata, min_pct=0.05, min_mean=0.1):
    
    ''' Returns var filtered adata and its bool mask.

    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks), typically for a certain celltype.
    min_pct : float
        Value between [0,1] of the minimum number of nonzero ATAC and RNA
        values for a given link.
    min_pct : float
        Minimum absolute mean for ATAC and RNA expression.
    
    Returns
    ----------
    ct_adata : ad.AnnData
        AnnData of shape (#cells,#peaks-peaks failing filter).
    lowexp_mask : np.array (dtype: bool)
        Boolean array of shape (#peaks) dictating which peaks were filtered out.

    '''

    # Check if at least min_pct of cells are nonzero and above min_mean for ATAC and RNA values
    lowexp_mask = filter_lowexp(adata.layers['atac_raw'], adata.layers['rna_raw'])

    # Filter out lowly expressed
    ct_adata = adata[:,lowexp_mask]
    
    return ct_adata, lowexp_mask



def filter_ct(adata, ct_key):
    
    ''' Returns CT-specific adata.
    
    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks).
    ct_key : int or str
        The key in the adata.uns['ct_labels'] dictionary for the desired celltype.
    
    Returns
    ----------
    ct_adata : ad.AnnData
        AnnData of shape (#cells within ct,#peaks) containing only cells of a given celltype. 

    '''
    
    # Get CT label
    ct = adata.uns['ct_labels'][ct_key]

    # Filter for CT
    ct_adata = adata[adata.obs['cell_type'] == ct].copy()

    # Remove general corrs if they exist
    try: 
        ct_adata.var.drop(columns=['corr','mc_pval'],inplace=True)
        del ct_adata.varm['control_corr']
    except KeyError as e: pass

    # Keep only specific label
    ct_adata.uns['ct_labels'].clear()
    ct_adata.uns['ct_labels'] = dict(ct_key=ct)
    
    return ct_adata



def build_ct_adata(adata, ct_key, min_pct=0.05, min_mean=0.1):

    ''' Returns CT-specific adata.
    
    Parameters
    ----------
    adata : ad.AnnData
        AnnData object of shape (#cells,#peaks).
    ct_key : int or str
        The key in the adata.uns['ct_labels'] dictionary for the desired celltype.
    
    Returns
    ----------
    ct_adata : ad.AnnData
        AnnData of shape (#cells within ct,#peaks) containing only cells of a given celltype,
        with lowly expressed links also filtered out. 

    '''
    
    ct_adata = filter_ct(adata,ct_key)
    ct_adata,ct_le_mask = filter_vars(ct_adata,min_pct,min_mean)

    # ct label
    ct = ct_adata.uns['ct_labels']['ct_key']
    
    # Add mask to original adata object for future reference
    adata.varm['lowexp_ct_mask'][ct] = ct_le_mask

    # we store original atac_X in uns for calculating the control corr 
    # as original control corr still includes peaks removed by lowexp masks
    ct_adata.uns['original_atac'] = adata.layers['atac'][(adata.obs['cell_type'] == ct),:]

    return ct_adata



############### poisson regr ######################################

def fit_poisson(x,y,return_none=True):

    # Simple log(E[y]) ~ x equation
    exog = sm.add_constant(x)
    
    try:
        poisson_model = sm.GLM(y, exog, family=sm.families.Poisson())
        result = poisson_model.fit()
        return result.params[1]

    # Remove failed MLE
    except Exception:
        if return_none:
            return None
        else:
            return 0


def get_poiss_coeff(adata,layer='raw',binarize=False,label='poiss_coeff'):

    # If binarizing, convert to bool then int
    # so that statsmodel can handle it
    if binarize:
        x = adata.layers['atac_'+layer].astype(bool).astype(int)
    else:
        x = adata.layers['atac_'+layer]
    if sp.sparse.issparse(x):
        x = x.A
    y = adata.layers['rna_'+layer]
    if sp.sparse.issparse(y):
        y = y.A

    coeffs = []
    failed = []

    # Calculate poisson coefficient for each peak gene pair
    for i in tqdm(np.arange(adata.shape[1])):
        coeff_ = fit_poisson(x[:,i],y[:, i])
        if not coeff_.any(): failed.append(i)
        else: coeffs.append(coeff_)

    # Remove pairs that have fail poisson
    idx = np.arange(adata.shape[1])
    idx = np.delete(idx,failed)
    adata = adata[:,idx].copy()
    # Save ATAC term coefficient in adata.var
    adata.var[label] = np.array(coeffs)

    return adata



############### delta corr ######################################

def build_other_adata(adata,ct_key):
    ''' Returns adata with everything except CT in ct_key.
    '''
    ct = adata.uns['ct_labels'][ct_key]
    
    other_adata = adata[~(adata.obs['celltype'] == ct),:]
    other_adata = other_adata[:,adata.varm['lowexp_ct_mask'][ct].values].copy()
    
    other_adata.var.drop(columns=['corr','mc_pval'],inplace=True)
    del other_adata.varm['control_corr']
    
    other_adata.uns['ct_labels'].clear()
    other_adata.uns['ct_labels'] = dict(ct_key=ct)

    # for other_adata, we store original atac_X in uns for calculating the control corr 
    # as original control corr still includes peaks removed by lowexp masks
    other_adata.uns['original_atac'] = adata.layers['atac'][~(adata.obs['celltype'] == ct),:]

    return other_adata

def get_deltas(ct_adata,other_adata):
    deltas = ct_adata.var['corr'].values - other_adata.var['corr'].values
    ct_adata.var['delta_corr'] = deltas
    return deltas

def get_control_deltas(ct_adata,other_adata):
    deltas = ct_adata.varm['control_corr'] - other_adata.varm['control_corr']
    ct_adata.varm['delta_control_corr'] = deltas
    return deltas



############# stratified corr #################################


def stratified_adata(adata,neighbors='leiden'):
    ''' Stratify ATAC and RNA information within neighborhood groupings by
    descending count.
    
    Parameters
    ----------
    adata : AnnData
        AnnData peak-gene linked object with layers ['atac_raw'] and ['rna_raw'].
    neighbors : str
        The column name in adata.obs containing neighborhood groupings.

    Returns
    -------
    adata : AnnData
        adata object with new layers ['atac_strat'] and ['rna_strat']. The vars axis
        will remain the same, while the obs axis will be sorted independently within
        neighborhood groupings in descending peak acc. and gene exp. order.
    
    '''
    
    # get the sizes of each neighborhood group
    neighborhood_sizes = adata.obs[neighbors].value_counts(sort=False)
    
    # sort adata by neighborhood grouping
    stratified_order = adata.obs[neighbors].sort_values().index
    sorted_adata = adata[stratified_order,:]
    atac = sorted_adata.layers['atac_raw'].A
    rna = sorted_adata.layers['rna_raw'].A

    stratified_atac, stratified_rna = build_strat_layers(atac, rna, neighborhood_sizes)

    adata.layers['atac_strat'] = stratified_atac
    adata.layers['rna_strat'] = stratified_rna

    return adata


def build_strat_layers(atac,rna,neighborhood_sizes):
    
    stratified_atac = []
    stratified_rna = []
    
    for i in neighborhood_sizes.values:
        
        # get first group of neighbors
        # sort in descending order
        atac_i = -np.sort(-atac[:i,:],axis=0)
        
        # remove the first group of neighbors
        atac = atac[i:,:]

        # append to list
        stratified_atac.append(atac_i)


        if rna is not None:

            rna_i = -np.sort(-rna[:i,:],axis=0)
            rna = rna[i:,:]
            stratified_rna.append(rna_i)

    # turn into arrays
    stratified_atac = np.vstack(stratified_atac)
    
    if rna is not None:
        stratified_rna = np.vstack(stratified_rna)
        return stratified_atac,stratified_rna

    return stratified_atac


def shuffled_poiss_coeff(adata,neighbors='leiden',b=200):

    ''' Shuffle peaks row-wise, then compute the poisson coefficient, b times.
    
    Parameters
    ----------
    adata : AnnData
        AnnData peak-gene linked object with layers ['atac_strat'] and ['rna_strat'].
    neighbors : str
        The column name in adata.obs containing neighborhood groupings.
    b : int
        The number of repetitions/times to shuffle.

    Returns
    -------
    coeffs : np.array
        Array of shape (#links,b), containing poisson coefficient for shuffled,
        stratified peak-gene pairs.
    
    '''

    # mantain neighborhood sizes from original adata.obs
    neighborhood_sizes = adata.obs[neighbors].value_counts(sort=False)

    coeffs = []
    
    for j in np.arange(b):
        
        atac = adata.layers['atac_strat']
        rna = adata.layers['rna_strat']
        
        # shuffle peaks only
        inds = np.arange(len(atac))
        np.random.shuffle(inds)
        atac = atac[inds]

        # arange shuffled rows into descending order within a strata
        atac = build_strat_layers(atac, None, neighborhood_sizes)
    
        coeffs_i = []
        for i in np.arange(adata.shape[1]):
            # obtain poisson coeff for all peak-gene pairs for shuffled cells 
            coeff_ = fit_poisson(atac[:,i],rna[:, i])
            coeffs_i.append(coeff_)
        coeffs_i = np.array(coeffs_i)
        coeffs.append(coeffs_i)
        
    coeffs = np.vstack(coeffs)

    return coeffs.T


