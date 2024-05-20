import numpy as np
import pandas as pd 
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
import muon as mu



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
    
    return np.abs(ctrls.flatten()), np.abs(main)


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
    del_cctrl_full_centered,del_c_centered = center_ctrls(del_cctrl_full,del_c)
    del_cctrl_full_centered = np.sort(del_cctrl_full_centered)
    n,b = del_cctrl_full.shape
    
    # Search sort returns indices where element would be inserted
    indicator = len(del_cctrl_full_centered) - np.searchsorted(del_cctrl_full_centered,del_c_centered)
    return (1+indicator)/(1+(n*b))





#####################################################################################
######################################## WIP ########################################
#####################################################################################


def find_peak_gene_pairs(mdata):
    
    '''Adds dataframe containing peak-gene pairs.

    Parameters
    ----------
    mdata : mu.MuData
        MuData object of shape (#cells,#peaks). Must contain atac.var with peaks listed under 'gene_ids'.
        Must also contain atac.uns['atac']['peak_annotation'] with peaks listed under 'peak' and genes
        listed under 'gene_name'. See mu.initialise_default_files.
    
    Returns
    ----------
    mdata : mu.MuData
        Updates mdata input with mdata.uns['peak_gene_pairs'], a pd.DataFrame of len (#peak-gene pairs)
        containing columns ['index_x','index_y'] that correspond to peak and gene indices in atac.X
        and rna.X respectively.
    
    '''
    
    rna = mdata.mod['rna']
    atac = mdata.mod['atac']

    try:
        atac.uns['atac']['peak_annotation']
    except KeyError:
        print('Must provide atac.uns[atac][peak_annotation].')
        raise

    try:
        atac.uns['atac']['peak_annotation']['gene_name']
    except KeyError:
        atac.uns['atac']['peak_annotation'] = atac.uns['atac']['peak_annotation'].reset_index(names='gene_name')
        print('Using peak_annotation index as gene_name column.')

    try:
        atac.uns['atac']['peak_annotation']['gene_ids']
    except KeyError:
        atac.uns['atac']['peak_annotation'].rename(columns={'peak':'gene_ids'},inplace=True)
        print('Using peak as gene_ids column.')

    # Merge the peak annotations. Reset index to maintain original atac.X index for reference
    mdata['atac'].var['index'] = range(len(atac.var))
    peak_gene_labels = pd.merge(atac.var[['gene_ids','index']], \
                            atac.uns['atac']['peak_annotation'], \
                            how='left',on='gene_ids')

    try:
        rna.var['gene_name']
    except KeyError:
        rna.var = rna.var.reset_index(names='gene_name')
        print('Using rna.var index as gene_name column.')
    
    # Duplicates for multiple peaks per gene, but drops ones not observed in RNA
    # Reset index to maintain original rna.X index for reference
    mdata['rna'].var['index'] = range(len(rna.var))
    peak_gene_pairs = pd.merge(peak_gene_labels,rna.var[['gene_name','index']], \
                           how='left',on='gene_name').dropna()

    mdata.uns['peak_gene_pairs'] = peak_gene_pairs

    return mdata



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



################ Building AnnData for corr ####################


def build_adata(mdata):
    
    '''Creates a new AnnData object for peak-gene links.

    Parameters
    ----------
    mdata : mu.MuData
        MuData object of shape (#cells,#peaks). Contains DataFrame under mdata.uns.peak_gene_pairs
        containing columns ['index_x','index_y'] that correspond to peak and gene indices in atac.X
        and rna.X respectively, as well as mdata.uns.control_peaks containing randomly generated peaks.
        If mdata.uns.control_peaks DNE, will create it.
    
    Returns
    ----------
    anadata : an.AnnData
        New AnnData object of shape (#cells with same ct between layers,#peak-gene pairs) and layers atac, rna.
        Obs are labelled by cell_id. Var are labelled by peak-gene links.
    
    '''

    try:
        mdata.uns['peak_gene_pairs']
    except KeyError:
        print('Attempting to add peak-gene pairs.')
        find_peak_gene_pairs(mdata)

    # Only take cells which match assigned celltypes between assays
    ct_mask = (mdata.obs['rna:celltype'] == mdata.obs['atac:celltype']).values

    # Initialize empty AnnData
    n = mdata[ct_mask,:].shape[0]
    m = len(mdata.uns['peak_gene_pairs'])
    anadata = ad.AnnData(np.zeros((n,m)))

    # Add aligned atac and rna layers. Should be CSC format.
    anadata.layers['atac'] = mdata['atac'].X[:,mdata.uns['peak_gene_pairs'].index_x.values][ct_mask,:]
    anadata.layers['rna'] = mdata['rna'].X[:,mdata.uns['peak_gene_pairs'].index_y.values][ct_mask,:]

    # Add aligned RAW atac and rna layers. Should be CSC format.
    anadata.layers['atac_raw'] = mdata['atac'].raw.X[:,mdata.uns['peak_gene_pairs'].index_x.values][ct_mask,:]
    anadata.layers['rna_raw'] = mdata['rna'].raw.X[:,mdata.uns['peak_gene_pairs'].index_y.values][ct_mask,:]

    # Add peak-gene pair descriptions
    mdata.uns['peak_gene_pairs']['id'] = list(mdata.uns['peak_gene_pairs'].gene_ids + ' , ' + mdata.uns['peak_gene_pairs'].gene_name)
    anadata.var = mdata.uns['peak_gene_pairs'].set_index('id')

    # Add celltypes, which should be the same between layers
    anadata.obs = mdata[ct_mask,:]['atac'].obs

    # Add empty placeholder for future CT masks (if any)
    anadata.varm['lowexp_ct_mask'] = pd.DataFrame(index=anadata.var.index)

    return anadata
    

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


def gc_content(adata,genome_file='GRCh38.p13.genome.fa.bgz'):
    
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


def get_bins(adata, num_bins=5):

    ''' Obtains GC and MFA bins for ATAC peaks.
    
    Parameters
    ----------
    adata : ad.AnnData
        AnnData
    num_bins : int
        Number of desired bins for MFA and GC groupings.
    
    Returns
    ----------
    bins : pd.DataFrame
        DataFrame of length (N) with columns ['gene_ids','index_x','mfa','gc','combined_mfa_gc']
    
    '''

    # Obtain mfa and gc content
    bins = adata.var[['gene_ids']].copy()
    bins['ind'] = range(len(bins))
    atac_X = adata.layers['atac']
    bins['mfa'] = atac_X.mean(axis=0).A1
    print('MFA done.')
    bins['gc'] = gc_content(adata)
    print('GC done.')
    
    # Put into bins
    bins['mfa_bin'] = pd.qcut(bins['mfa'].rank(method='first'), num_bins, labels=False, duplicates="drop")
    bins['gc_bin'] = pd.qcut(bins['gc'].rank(method='first'), num_bins, labels=False, duplicates="drop")

    # Create combined bin
    bins['combined_mfa_gc']=bins['mfa_bin']* 10 + bins['gc_bin']
    
    return bins


def rand_peaks(row,df=None,b=1000):

    ''' Function applied row-wise to select random peaks.
    
    Parameters
    ----------
    row : row of pd.DataFrame
        A row of a pd.DataFrame containing ['combined_mfa_gc'] (bin ID) column and
        ['index_x'] (peak indices).
    df : pd.DataFrame
        DataFrame of length (#bins) where each value corresponds to a list containing 
        all possible ['index_x'] (peak indices) for a given MFA and GC bin.
    b : int
        Number of desired random peaks for each putative peak. (B)
    
    Returns
    ----------
    row_rand_peaks : np.array
        Array of length (B,) with randomly sampled peaks for the given row.
    
    '''  

    # Find corresponding bin for focal peak
    row_bin = df.loc[row.combined_mfa_gc]
    
    # Exclude main peak
    row_bin_copy = row_bin[row_bin!=row.name]
    
    # Return b randomly sampled peaks from that bin
    return random.sample(row_bin_copy['ind'], k=b)


def create_ctrl_peaks(adata,num_bins=5,b=1000,update=True):

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
    
    Returns
    ----------
    ctrl_peaks : np.ndarray
        Matrix of length (#peaks,n) where n is number of random peaks generated (1000 by default).
    
    '''
    
    bins = get_bins(adata, num_bins=5)
    print('Get_bins done.')
    
    # Group indices for rand_peaks
    bins_grouped = bins[['ind','combined_mfa_gc']].groupby(['combined_mfa_gc']).agg(list)
    
    # Generate random peaks
    ctrl_peaks = bins.apply(rand_peaks,axis=1,df=bins_grouped,b=b)
    print('Rand_peaks done.')
    
    # Make into array
    ctrl_peaks = ctrl_peaks.apply(lambda x: np.array(x))
    ctrl_peaks = np.vstack(ctrl_peaks.array)
    print('Ctrl index array done.')

    if update:
        # Add mdata.uns.control_peaks
        adata.varm['control_peaks'] = ctrl_peaks
    
    return ctrl_peaks


def control_corr(adata, b=1000, update=True, ct=False):

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
        atac_Xs = adata.layers['atac']
    rna_Xs = adata.layers['rna']
    
    ctrl_corr = np.empty([adata.shape[1], b])
    for row in tqdm(range(adata.shape[1])):
        ctrl_corr[row,:] = pearson_corr_sparse(atac_Xs[:,ctrl_peaks[row]],rna_Xs[:,[row]])

    if update:
        adata.varm['control_corr'] = ctrl_corr
    
    return ctrl_corr


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

# TO DO: add annotations for this section


def filter_vars(adata, min_cells=10, min_mean=0.1):
    
    ''' Returns var filtered adata and its bool mask.
    '''
    
    lowexp_mask = (((adata.layers['atac'].mean(axis=0) > min_mean) & (adata.layers['rna'].mean(axis=0) > min_mean)) & \
                   ((adata.layers['atac_raw'].getnnz(axis=0) > min_cells) & \
                    (adata.layers['rna_raw'].getnnz(axis=0) > min_cells))).A1
    
    ct_adata = adata[:,lowexp_mask]

    return ct_adata, lowexp_mask

def filter_ct(adata, ct_key):
    
    ''' Returns CT-specific adata.
    '''
    
    # get CT label
    ct = adata.uns['ct_labels'][ct_key]

    # filter for CT
    ct_adata = adata[adata.obs['celltype'] == ct].copy()

    # remove general corrs
    ct_adata.var.drop(columns=['corr','mc_pval'],inplace=True)
    del ct_adata.varm['control_corr']

    ### to do: add something to check if these features are there in the first place

    # keep only specific label
    ct_adata.uns['ct_labels'].clear()
    ct_adata.uns['ct_labels'] = dict(ct_key=ct)
    
    return ct_adata

def build_ct_adata(adata, ct_key):
    
    ct_adata = filter_ct(adata,ct_key)
    ct_adata,ct_le_mask = filter_vars(ct_adata)

    # ct label
    ct = ct_adata.uns['ct_labels']['ct_key']
    
    # Add mask to original adata object for future reference
    adata.varm['lowexp_ct_mask'][ct] = ct_le_mask

    # we store original atac_X in uns for calculating the control corr 
    # as original control corr still includes peaks removed by lowexp masks
    ct_adata.uns['original_atac'] = adata.layers['atac'][(adata.obs['celltype'] == ct),:]
    
    return ct_adata

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