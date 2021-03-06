# integration test ---  test the solver, datasaver modules in tango

from __future__ import division
import numpy as np
import os
import h5py
import scipy
import scipy.integrate

from tango.extras import shestakov_nonlinear_diffusion
import tango


def test_solver_basic():
    # test the use of solver class   
    (L, N, dx, x, nL, n, maxIterations, tol, fields, compute_all_H_all_fields, tArray) = problem_setup()
    
    solver = tango.solver.Solver(L, x, tArray, maxIterations, tol, compute_all_H_all_fields, fields)
    
    # set up data logger
    while solver.ok:
        # Implicit time advance: iterate to solve the nonlinear equation!
        solver.take_timestep()
        
    n = solver.profiles['n']  # finished solution
    # compare with analytic steady state solution
    nss = shestakov_nonlinear_diffusion.GetSteadyStateSolution(x, nL)
    solution_residual = (n - nss) / np.max(np.abs(nss))
    solution_rms_error = np.sqrt( 1/len(n) * np.sum(solution_residual**2))
    
    obs = solution_rms_error
    exp = 0
    testtol = 1e-3
    assert abs(obs - exp) < testtol

def test_three_fields():
    # test Tango with 3 coupled fields
    L, N, dx, x, nL, n = initialize_shestakov_problem()

    n_IC, pi_IC, pe_IC = 1.0*n, 1.0*n, 1.0*n
    
    n_L, pi_L, pe_L = 2, 0.1, 0.3    
    nu0 = 2.2
    maxIterations, lmParams, tol = initialize_parameters()
    
    label0, label1, label2 = 'n', 'pi', 'pe'
    
    # set up for n
    compute_all_H_n = ComputeAllH_n()
    lm_n = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    field0 = tango.multifield.Field(label=label0, rightBC=n_L, profile_mminus1=n_IC, compute_all_H=compute_all_H_n, lodestroMethod=lm_n)
    
    # set up for pi
    compute_all_H_pi = ComputeAllH_pi(nu0)
    lm_pi = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    field1 = tango.multifield.Field(label=label1, rightBC=pi_L, profile_mminus1=pi_IC, compute_all_H=compute_all_H_pi, lodestroMethod=lm_pi, coupledTo='pe')
    
    # set up for pe
    compute_all_H_pe = ComputeAllH_pe(nu0)
    lm_pe = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    field2 = tango.multifield.Field(label=label2, rightBC=pe_L, profile_mminus1=pe_IC, compute_all_H=compute_all_H_pe, lodestroMethod=lm_pe, coupledTo='pi')
    
    # combine fields and do checking
    fields = [field0, field1, field2]
    tango.multifield.check_fields_initialize(fields)
    
    # create the flux model and the turbulence handler
    fluxModel = ShestakovThreeFieldFluxModel(dx)
    turbHandler = tango.lodestro_method.TurbulenceHandler(dx, x, fluxModel)
    compute_all_H_all_fields = tango.multifield.ComputeAllHAllFields(fields, turbHandler)
    
    tArray = np.array([0, 1e6])  # specify the timesteps to be used.
    
    # initialize the solver
    solver = tango.solver.Solver(L, x, tArray, maxIterations, tol, compute_all_H_all_fields, fields)
    
    while solver.ok:
    # Implicit time advance: iterate to solve the nonlinear equation!
        solver.take_timestep()

    # do some checking
    n = solver.profiles[label0]    
    pi = solver.profiles[label1]
    pe = solver.profiles[label2]
    
    fluxes = fluxModel.get_flux(solver.profiles)
    Gamma = fluxes['n']
    Qi = fluxes['pi']
    Qe = fluxes['pe']
    
    RHSn = source_n(x)
    RHSi = source_i(x) - calc_nu(nu0, n, pi, pe) * (pi - pe)
    RHSe = source_e(x) - calc_nu(nu0, n, pi, pe) * (pe - pi)
    
    RHSn_integrated = scipy.integrate.cumtrapz(RHSn, x=x, initial=0)
    RHSi_integrated = scipy.integrate.cumtrapz(RHSi, x=x, initial=0)
    RHSe_integrated = scipy.integrate.cumtrapz(RHSe, x=x, initial=0)
    
    # check fluxes are close to what they should be
    assert np.allclose(Gamma, RHSn_integrated, rtol=1e-2, atol=0.02)
    assert np.allclose(Qi, RHSi_integrated, rtol=1e-2, atol=0.02)
    assert np.allclose(Qe, RHSe_integrated, rtol=1e-2, atol=0.02)
    
def test_inner_iteration_loop():
    # test Tango with 3 coupled fields and an inner iteration loop for nonlinear terms other than turbulent flux
    L, N, dx, x, nL, n = initialize_shestakov_problem()

    n_IC, pi_IC, pe_IC = 1.0*n, 1.0*n, 1.0*n
    
    n_L, pi_L, pe_L = 2, 0.1, 0.3    
    nu0 = 2.2
    maxIterations, lmParams, tol = initialize_parameters()
    
    label0, label1, label2 = 'n', 'pi', 'pe'
    
    # set up for n
    compute_all_H_n = ComputeAllH_n()
    lm_n = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    field0 = tango.multifield.Field(label=label0, rightBC=n_L, profile_mminus1=n_IC, compute_all_H=compute_all_H_n, lodestroMethod=lm_n)
    
    # set up for pi
    compute_all_H_pi = ComputeAllH_pi(nu0)
    lm_pi = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    field1 = tango.multifield.Field(label=label1, rightBC=pi_L, profile_mminus1=pi_IC, compute_all_H=compute_all_H_pi, lodestroMethod=lm_pi, coupledTo='pe')
    
    # set up for pe
    compute_all_H_pe = ComputeAllH_pe(nu0)
    lm_pe = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    field2 = tango.multifield.Field(label=label2, rightBC=pe_L, profile_mminus1=pe_IC, compute_all_H=compute_all_H_pe, lodestroMethod=lm_pe, coupledTo='pi')
    
    # combine fields and do checking
    fields = [field0, field1, field2]
    tango.multifield.check_fields_initialize(fields)
    
    # create the flux model and the turbulence handler
    fluxModel = ShestakovThreeFieldFluxModel(dx)
    turbHandler = tango.lodestro_method.TurbulenceHandler(dx, x, fluxModel)
    compute_all_H_all_fields = tango.multifield.ComputeAllHAllFields(fields, turbHandler)
    
    tArray = np.array([0, 1e6])  # specify the timesteps to be used.
    
    # initialize the solver
    solver = tango.solver.Solver(L, x, tArray, maxIterations, tol, compute_all_H_all_fields, fields, useInnerIteration=True, innerIterationMaxCount=2)
    
    while solver.ok:
    # Implicit time advance: iterate to solve the nonlinear equation!
        solver.take_timestep()

    # do some checking
    n = solver.profiles[label0]    
    pi = solver.profiles[label1]
    pe = solver.profiles[label2]
    
    fluxes = fluxModel.get_flux(solver.profiles)
    Gamma = fluxes['n']
    Qi = fluxes['pi']
    Qe = fluxes['pe']
    
    RHSn = source_n(x)
    RHSi = source_i(x) - calc_nu(nu0, n, pi, pe) * (pi - pe)
    RHSe = source_e(x) - calc_nu(nu0, n, pi, pe) * (pe - pi)
    
    RHSn_integrated = scipy.integrate.cumtrapz(RHSn, x=x, initial=0)
    RHSi_integrated = scipy.integrate.cumtrapz(RHSi, x=x, initial=0)
    RHSe_integrated = scipy.integrate.cumtrapz(RHSe, x=x, initial=0)
    
    # check fluxes are close to what they should be
    assert np.allclose(Gamma, RHSn_integrated, rtol=1e-2, atol=0.02)
    assert np.allclose(Qi, RHSi_integrated, rtol=1e-2, atol=0.02)
    assert np.allclose(Qe, RHSe_integrated, rtol=1e-2, atol=0.02)    
    
#def test_solver_multiple_files():
#    # test the use of solver class with data logger --- multiple files from multiple timesteps
#    (L, N, dx, x, nL, n, maxIterations, tol, turbhandler, compute_all_H, t_array) = problem_setup()
#    t_array = np.array([0, 1.0, 1e4])
#    solver = tango.solver.Solver(L, x, n, nL, t_array, maxIterations, tol, compute_all_H, turbhandler)
#    
#    # set up data logger
#    arrays_to_save = ['H2', 'H3', 'profile']
#    databasename = 'test_integration_data'
#    solver.dataSaverHandler.initialize_datasaver(databasename, maxIterations, arrays_to_save)
#    while solver.ok:
#        # Implicit time advance: iterate to solve the nonlinear equation!
#        solver.take_timestep()
#        
#    n = solver.profile  # finished solution
#    
#    datasavename1_iterations = databasename + "1_iterations.npz"
#    datasavename2_timestep = databasename + "2_timestep.npz"
#    with np.load(datasavename1_iterations) as npzfile:
#        H2 = npzfile['H2']
#        (temp, H2N) = np.shape(H2)
#    with np.load(datasavename2_timestep) as npzfile:
#        n_loaded = npzfile['profile_m']
#    assert N == H2N
#    assert(np.allclose(n, n_loaded, rtol=0, atol=1e-15))
#    os.remove(datasavename1_iterations)
#    os.remove(databasename + "1_timestep.npz")
#    os.remove(datasavename2_timestep)
#    os.remove(databasename + "2_iterations.npz")

def test_solver_not_converging():
    # test that data is stored even when solution does not converge within MaxIterations
    (L, N, dx, x, nL, n, maxIterations, tol, fields, compute_all_H_all_fields, tArray) = problem_setup()
    maxIterations = 60  # takes 85 iterations to converge for these parameters
    
    # set up filehandler
    setNumber = 0
    xTango = x
    xTurb = x
    t = tArray[1]
    initialData = tango.handlers.TangoHistoryHandler.set_up_initialdata(setNumber, xTango, xTurb, t, fields)
    
    basename = 'tangodata'
    tangoHistoryHandler = tango.handlers.TangoHistoryHandler(iterationInterval=1, basename=basename, maxIterations=1000, initialData=initialData)
    filename = basename + '_s{}'.format(setNumber) + '.hdf5'
    
    solver = tango.solver.Solver(L, x, tArray, maxIterations, tol, compute_all_H_all_fields, fields)
    solver.fileHandlerExecutor.add_handler(tangoHistoryHandler)
    
    while solver.ok:
        # Implicit time advance: iterate to solve the nonlinear equation!
        solver.take_timestep()
    
    n = solver.profiles['n']
    with h5py.File(filename) as f:
        assert len(f['iterationNumber']) == maxIterations
        n_loaded = f['n/profile'][-1]
        assert np.allclose(n, n_loaded, rtol=0, atol=1e-15)
        
    # teardown
    os.remove(filename)
    
def test_solver_small_ewma_param():
    """Test that proper convergence is reached for small EWMA parameters.  Previously, a bug prevented
    full convergence for EWMAParam <~ 0.01 but worked at larger values."""    
    L, N, dx, x, nL, n = initialize_shestakov_problem()
    junk, lmParams, junk2 = initialize_parameters()
    maxIterations = 4000
    tol = 1e-9
    
    # adjust the EWMA parameter
    EWMAParam = 0.01
    lmParams['EWMAParamTurbFlux'] = EWMAParam
    lmParams['EWMAParamProfile'] = EWMAParam
    
    compute_all_H = ComputeAllH()
    lm = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    field = tango.multifield.Field(label='n', rightBC=nL, profile_mminus1=n, compute_all_H=compute_all_H, lodestroMethod=lm)
    fields = [field]
    tango.multifield.check_fields_initialize(fields)
    fluxModel = shestakov_nonlinear_diffusion.AnalyticFluxModel(dx)
    turbHandler = tango.lodestro_method.TurbulenceHandler(dx, x, fluxModel)
    compute_all_H_all_fields = tango.multifield.ComputeAllHAllFields(fields, turbHandler)
    tArray = np.array([0, 1e4])  # specify the timesteps to be used.    
    solver = tango.solver.Solver(L, x, tArray, maxIterations, tol, compute_all_H_all_fields, fields)
    
    while solver.ok:
        # Implicit time advance: iterate to solve the nonlinear equation!
        solver.take_timestep()
        
    selfConsistencyErrorFinal = solver.errHistoryFinal[-1]
    assert selfConsistencyErrorFinal <= tol
    
#def test_solver_user_control_func():
#    """Test the use of the user control function.  Here, change the EWMA parameter in the course of solving
#    at specific iterations.    
#    """
#    L, N, dx, x, nL, n = initialize_shestakov_problem()
#    junk, lmParams, junk2 = initialize_parameters()
#    
#    maxIterations = 10
#    tol = 1e-9
#    label = 'n'
#    
#    compute_all_H = ComputeAllH()
#    lm = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
#    field = tango.multifield.Field(label=label, rightBC=nL, profile_mminus1=n, compute_all_H=compute_all_H, lodestroMethod=lm)
#    fields = [field]
#    tango.multifield.check_fields_initialize(fields)
#    
#    fluxModel = shestakov_nonlinear_diffusion.AnalyticFluxModel(dx)
#    turbHandler = tango.lodestro_method.TurbulenceHandler(dx, x, fluxModel)
#
#    compute_all_H_all_fields = tango.multifield.ComputeAllHAllFields(fields, turbHandler)
#
#    # initialize the solver
#    tArray = np.array([0, 1e4])  # specify the timesteps to be used.
#    user_control_func = UserControlFunc(turbHandler)
#    solver = tango.solver.Solver(L, x, tArray, maxIterations, tol, compute_all_H_all_fields, fields, user_control_func)
#    
#    expEWMAParamStart = lmParams['EWMAParamTurbFlux']
#    (obsEWMAParamStart, junk) = turbHandler.get_ewma_params()
#    assert expEWMAParamStart == obsEWMAParamStart
#    
#    while solver.ok:
#        # Implicit time advance: iterate to solve the nonlinear equation!
#        solver.take_timestep()
#        
#    expEWMAParamFinish = 0.13
#    (obsEWMAParamFinish, junk) = turbHandler.get_ewma_params()
#    assert expEWMAParamFinish == obsEWMAParamFinish
    
#==============================================================================
#    End of tests.  Below are helper functions used by the tests
#==============================================================================

def problem_setup():    
    L, N, dx, x, nL, n = initialize_shestakov_problem()
    maxIterations, lmParams, tol = initialize_parameters()
    compute_all_H = ComputeAllH()
    lm = tango.lodestro_method.lm(lmParams['EWMAParamTurbFlux'], lmParams['EWMAParamProfile'], lmParams['thetaParams'])
    field = tango.multifield.Field(label='n', rightBC=nL, profile_mminus1=n, compute_all_H=compute_all_H, lodestroMethod=lm)
    fields = [field]
    tango.multifield.check_fields_initialize(fields)
    fluxModel = shestakov_nonlinear_diffusion.AnalyticFluxModel(dx)
    turbHandler = tango.lodestro_method.TurbulenceHandler(dx, x, fluxModel)
    compute_all_H_all_fields = tango.multifield.ComputeAllHAllFields(fields, turbHandler)
    tArray = np.array([0, 1e4])  # specify the timesteps to be used.    
    return (L, N, dx, x, nL, n, maxIterations, tol, fields, compute_all_H_all_fields, tArray)
    

def initialize_shestakov_problem():
    # Problem Setup
    L = 1           # size of domain
    N = 500         # number of spatial grid points
    dx = L / (N-1)  # spatial grid size
    x = np.arange(N)*dx # location corresponding to grid points j=0, ..., N-1
    nL = 1e-2           # right boundary condition
    n_initialcondition = 1 - 0.5*x
    return (L, N, dx, x, nL, n_initialcondition)

def initialize_parameters():
    maxIterations = 1000
    thetaParams = {'Dmin': 1e-5,
                   'Dmax': 1e13,
                   'dpdxThreshold': 10}
    EWMAParamTurbFlux = 0.30
    EWMAParamProfile = 0.30
    lmParams = {'EWMAParamTurbFlux': EWMAParamTurbFlux,
            'EWMAParamProfile': EWMAParamProfile,
            'thetaParams': thetaParams}
    tol = 1e-11  # tol for convergence... reached when a certain error < tol
    return (maxIterations, lmParams, tol)

class ComputeAllH(object):
    def __init__(self):
        pass
    def __call__(self, t, x, profiles, HCoeffsTurb):
        # Define the contributions to the H coefficients for the Shestakov Problem
        H1 = np.ones_like(x)
        H7 = shestakov_nonlinear_diffusion.H7contrib_Source(x)
        
        HCoeffs = tango.multifield.HCoefficients(H1=H1, H7=H7)
        HCoeffs = HCoeffs + HCoeffsTurb
        return HCoeffs

#class UserControlFunc(object):
#    def __init__(self, turbhandler):
#        self.turbhandler = turbhandler
#    def __call__(self, solver):
#        """
#        User Control Function for the solver.
#        
#        Here, modify the EWMA paramater as the iteration number increases to converge quickly at the beginning and then to get more
#        averaging towards the end.
#        
#        Inputs:
#          solver            tango Solver (object)
#        """
#        iterationNumber = solver.l
#        if iterationNumber == 5:
#            self.turbhandler.set_ewma_params(0.13, 0.13)

#==============================================================================
#    Helper functions for the three-field test
#==============================================================================

class ShestakovThreeFieldFluxModel(object):
    def __init__(self, dx):
        self.dx = dx
    def get_flux(self, profiles):
        n = profiles['n']
        pi = profiles['pi']
        pe = profiles['pe']
    
        # Return flux Gamma on the same grid as n
        dndx = tango.derivatives.dx_centered_difference_edge_first_order(n, self.dx)
        dpidx = tango.derivatives.dx_centered_difference_edge_first_order(pi, self.dx)
        dpedx = tango.derivatives.dx_centered_difference_edge_first_order(pe, self.dx)
        D = dpidx**2 / pi**2
        Gamma = -D * dndx
        Qi = -D * dpidx
        Qe = -D * dpedx
        
        fluxes = {}
        fluxes['n'] = Gamma
        fluxes['pi'] = Qi
        fluxes['pe'] = Qe
        return fluxes

def source_n(x, S0=8, delta=0.3):
    """Return the source S_n."""
    S = np.zeros_like(x)
    S[x < delta] = S0
    return S
        
def source_i(x, S0=1, delta=0.1):
    """Return the source S_i."""
    S = np.zeros_like(x)
    S[x < delta] = S0
    return S
    
def source_e(x, S0=3, delta=0.4):
    """Return the source S_e."""
    S = np.zeros_like(x)
    ind = x < delta
    S[ind] = S0 * x[ind]
    return S
    
    
class ComputeAllH_n(object):
    def __call__(self, t, x, profiles, HCoeffsTurb):
        #pi = profiles['pi']
        #pe = profiles['pe']
        #n = profiles['field0']
        # Define the contributions to the H coefficients for the Shestakov Problem
        H1 = np.ones_like(x)
        H7 = source_n(x)
        
        HCoeffs = tango.multifield.HCoefficients(H1=H1, H7=H7)
        HCoeffs = HCoeffs + HCoeffsTurb
        return HCoeffs
    
def calc_nu(nu0, n, pi, pe):
    nu = nu0 / (( pi/n + pe/n) ** (3/2) )
    return nu
        
class ComputeAllH_pi(object):
    def __init__(self, nu0):
        self.nu0 = nu0
    def __call__(self, t, x, profiles, HCoeffsTurb):
        n = profiles['n']
        pi = profiles['pi']
        pe = profiles['pe']
        #n = profiles['field0']
        # Define the contributions to the H coefficients for the Shestakov Problem
        H1 = np.ones_like(x)
        H7 = source_i(x)
        
        nu = calc_nu(self.nu0, n, pi, pe)
        H6 = -nu
        H8 = nu
        
        HCoeffs = tango.multifield.HCoefficients(H1=H1, H6=H6, H7=H7, H8=H8)
        HCoeffs = HCoeffs + HCoeffsTurb
        return HCoeffs
        
class ComputeAllH_pe(object):
    def __init__(self, nu0):
        self.nu0 = nu0
    def __call__(self, t, x, profiles, HCoeffsTurb):
        n = profiles['n']
        pi = profiles['pi']
        pe = profiles['pe']
        # Define the contributions to the H coefficients for the Shestakov Problem
        H1 = np.ones_like(x)
        H7 = source_e(x)
        
        nu = calc_nu(self.nu0, n, pi, pe)
        H6 = -nu
        H8 = nu
        
        HCoeffs = tango.multifield.HCoefficients(H1=H1, H6=H6, H7=H7, H8=H8)
        HCoeffs = HCoeffs + HCoeffsTurb
        return HCoeffs